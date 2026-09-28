"""
internal/data/vector_store/pgvector.py — PGVector 向量存储实现

基于 PostgreSQL + pgvector 扩展的向量存储，支持相似度检索。

表结构由 ensure_pgvector_schema() 在启动 / migrate 时幂等创建。
"""

from __future__ import annotations

import json
import re
import structlog

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from internal.biz.repo import VectorStore


logger = structlog.get_logger()

_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DEFAULT_TABLE_NAME = "chunk_vectors"


def _validate_dimension(dimension: int) -> int:
    if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension < 1 or dimension > 4096:
        raise ValueError(f"embedding dimension must be an int in 1..4096, got {dimension!r}")
    return dimension


def _validate_table_name(table_name: str) -> str:
    if not _IDENT_RE.match(table_name):
        raise ValueError(f"invalid vector table name: {table_name!r}")
    return table_name


def build_pgvector_ddl(
    dimension: int = 1024,
    table_name: str = DEFAULT_TABLE_NAME,
) -> list[str]:
    """生成幂等 DDL：扩展、表、document 索引、HNSW 向量索引、metadata GIN。"""
    dim = _validate_dimension(dimension)
    table = _validate_table_name(table_name)
    return [
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"""
            CREATE TABLE IF NOT EXISTS {table} (
                chunk_id BIGINT PRIMARY KEY,
                document_id BIGINT NOT NULL,
                embedding vector({dim}) NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{table}_document ON {table} (document_id)",
        f"CREATE INDEX IF NOT EXISTS idx_{table}_embedding ON {table} USING hnsw (embedding vector_cosine_ops)",
        f"CREATE INDEX IF NOT EXISTS idx_{table}_metadata ON {table} USING gin (metadata)",
    ]


async def ensure_pgvector_schema(
    engine: AsyncEngine,
    dimension: int = 1024,
    table_name: str = DEFAULT_TABLE_NAME,
) -> None:
    """幂等创建 pgvector 扩展与 chunk_vectors 表。仅用于 PostgreSQL。"""
    if engine.dialect.name != "postgresql":
        logger.info(
            "pgvector_schema_skipped",
            dialect=engine.dialect.name,
            hint="pgvector schema is PostgreSQL-only",
        )
        return

    statements = build_pgvector_ddl(dimension=dimension, table_name=table_name)
    async with engine.begin() as conn:
        for sql in statements:
            await conn.execute(text(sql))

    logger.info(
        "pgvector_schema_ready",
        table=table_name,
        dimension=dimension,
    )


class PGVectorStore(VectorStore):
    """PGVector 向量存储 — PostgreSQL + pgvector 扩展。

    使用原生 SQL 操作 pgvector 类型，避免额外 Python 依赖。
    需要数据库已安装 pgvector 扩展并创建对应表（ensure_pgvector_schema）。
    """

    def __init__(
        self,
        session_factory,
        dimension: int = 1024,
        table_name: str = DEFAULT_TABLE_NAME,
    ) -> None:
        self._session_factory = session_factory
        self._dimension = _validate_dimension(dimension)
        self._table_name = _validate_table_name(table_name)

    async def upsert(
        self,
        chunk_ids: list[int],
        vectors: list[list[float]],
        metadatas: list[dict] | None = None,
    ) -> None:
        """插入或更新向量（UPSERT）。"""
        if not chunk_ids:
            return
        if len(vectors) != len(chunk_ids):
            raise ValueError(
                f"vector count ({len(vectors)}) does not match chunk_id count ({len(chunk_ids)})"
            )
        for i, vec in enumerate(vectors):
            if len(vec) != self._dimension:
                raise ValueError(
                    f"vector dim {len(vec)} != configured dimension {self._dimension} "
                    f"(chunk_id={chunk_ids[i]})"
                )

        async with self._session_factory() as session:
            for i, cid in enumerate(chunk_ids):
                vec = vectors[i]
                meta = metadatas[i] if metadatas else {}
                vec_str = "[" + ",".join(str(v) for v in vec) + "]"
                meta_json = json.dumps(meta, separators=(",", ":"), ensure_ascii=False)

                # 有则更新：INSERT ... ON CONFLICT DO UPDATE
                # 注意：text() 会把 ":embedding::vector" 误解析为两个绑定参数
                # (:embedding 与 :vector)，必须用 CAST(:x AS type) 形式避免。
                sql = f"""
                    INSERT INTO {self._table_name} (chunk_id, document_id, embedding, metadata)
                    VALUES (:chunk_id, :document_id, CAST(:embedding AS vector), CAST(:metadata AS jsonb))
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        metadata = EXCLUDED.metadata
                """
                await session.execute(
                    text(sql),
                    {
                        "chunk_id": cid,
                        "document_id": meta.get("document_id", 0),
                        "embedding": vec_str,
                        "metadata": meta_json,
                    },
                )
            await session.commit()

        logger.debug("pgvector_upsert", count=len(chunk_ids))

    async def delete(self, chunk_ids: list[int]) -> None:
        """删除指定分块的向量。"""
        if not chunk_ids:
            return

        async with self._session_factory() as session:
            sql = f"DELETE FROM {self._table_name} WHERE chunk_id = ANY(:ids)"
            await session.execute(text(sql), {"ids": chunk_ids})
            await session.commit()

        logger.debug("pgvector_delete", count=len(chunk_ids))

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filter_metadata: dict | None = None,
    ) -> list[tuple[int, float]]:
        """余弦相似度搜索（pgvector 原生）。

        在不引入额外依赖的情况下，支持按 JSONB 元数据做简单等值过滤：
        使用 `metadata @> :filter::jsonb` 进行包含匹配（键全等）。
        """
        vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"

        where_clause = ""
        params = {"query": vec_str, "top_k": top_k}
        if filter_metadata:
            filter_json = json.dumps(filter_metadata, separators=(",", ":"), ensure_ascii=False)
            # 同 upsert：必须用 CAST(:filter AS jsonb) 避免 text() 误解析 ::jsonb
            where_clause = " WHERE metadata @> CAST(:filter AS jsonb)"
            params["filter"] = filter_json

        async with self._session_factory() as session:
            sql = f"""
                SELECT chunk_id, 1 - (embedding <=> CAST(:query AS vector)) AS score
                FROM {self._table_name}
                {where_clause}
                ORDER BY embedding <=> CAST(:query AS vector)
                LIMIT :top_k
            """
            result = await session.execute(text(sql), params)
            return [(row.chunk_id, row.score) for row in result]
