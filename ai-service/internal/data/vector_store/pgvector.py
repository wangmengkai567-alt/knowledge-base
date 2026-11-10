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

# 表名校验规则。
_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# 默认向量表名。
DEFAULT_TABLE_NAME = "chunk_vectors"


def _validate_dimension(dimension: int) -> int:
    """校验向量维度为 1～4096 的整数。"""
    if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension < 1 or dimension > 4096:
        raise ValueError(f"embedding dimension must be an int in 1..4096, got {dimension!r}")
    return dimension


def _validate_table_name(table_name: str) -> str:
    """校验表名格式。"""
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
    """在 PostgreSQL 中创建向量扩展、指定名称的表及索引；已存在时跳过。"""
    # pgvector 仅支持 PostgreSQL，其他数据库直接跳过。
    if engine.dialect.name != "postgresql":
        logger.info(
            "pgvector_schema_skipped",
            dialect=engine.dialect.name,
            hint="pgvector schema is PostgreSQL-only",
        )
        return

    # 根据向量维度和表名生成创建扩展、表及索引的 SQL 语句。
    statements = build_pgvector_ddl(dimension=dimension, table_name=table_name)
    # 连接数据库并开启事务，依次执行上面生成的 SQL。
    async with engine.begin() as conn:
        for sql in statements:
            # PostgreSQL 执行当前语句，创建对应的扩展、表或索引。
            await conn.execute(text(sql))
    # 全部成功后事务提交；执行出错时事务回滚，异常向上传播。

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
        """配置数据库会话工厂、向量维度和表名；维度须与模型及表结构一致。"""
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

    # 核实向量确实存在且属于指定文档和知识库，供入库状态检查使用。
    async def existing_ids(self, chunk_ids: list[int], document_id: int, knowledge_base_id: int) -> set[int]:
        if not chunk_ids:
            return set()
        async with self._session_factory() as session:
            rows = await session.execute(text(
                f"SELECT chunk_id FROM {self._table_name} WHERE chunk_id = ANY(:ids) "
                "AND document_id = :doc_id "
                "AND metadata @> CAST(:meta AS jsonb)"
            ), {"ids": chunk_ids, "doc_id": document_id,
                "meta": json.dumps({"document_id": document_id, "knowledge_base_id": knowledge_base_id})})
            return set(rows.scalars())

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filter_metadata: dict | None = None,
        *,
        document_ids: list[int] | None = None,
    ) -> list[tuple[int, float]]:
        """在文档范围与 JSONB 等值条件筛选后，对合格向量按余弦相似度排序。

        返回前 top_k 个 (chunk_id, score)；先物化筛选集合，再执行精确排序。
        """
        vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"

        if document_ids == []:
            return []

        predicates = []
        params = {"query": vec_str, "top_k": top_k}
        if document_ids is not None:
            predicates.append("document_id = ANY(:document_ids)")
            params["document_ids"] = document_ids
        if filter_metadata:
            filter_json = json.dumps(filter_metadata, separators=(",", ":"), ensure_ascii=False)
            # 同 upsert：必须用 CAST(:filter AS jsonb) 避免 text() 误解析 ::jsonb
            predicates.append("metadata @> CAST(:filter AS jsonb)")
            params["filter"] = filter_json
        where_clause = "WHERE " + " AND ".join(predicates) if predicates else ""

        async with self._session_factory() as session:
            # 先物化可用集合，再精确排序。直接在 HNSW 扫描后过滤仍可能耗尽候选。
            sql = f"""
                WITH eligible AS MATERIALIZED (
                    SELECT chunk_id, embedding
                    FROM {self._table_name}
                    {where_clause}
                )
                SELECT chunk_id, 1 - (embedding <=> CAST(:query AS vector)) AS score
                FROM eligible
                ORDER BY embedding <=> CAST(:query AS vector)
                LIMIT :top_k
            """
            result = await session.execute(text(sql), params)
            return [(row.chunk_id, row.score) for row in result]
