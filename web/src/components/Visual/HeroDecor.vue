<script setup lang="ts">
/** 首页 Hero 右侧装饰：球体 + 文档浮卡，跟随父级 --px/--py。 */
</script>

<template>
  <div class="hero-decor" aria-hidden="true">
    <div class="hero-decor__glow" />
    <div class="hero-decor__sphere" />
    <div class="hero-decor__card hero-decor__card--a">
      <span />
      <span />
    </div>
    <div class="hero-decor__card hero-decor__card--b" />
    <div class="hero-decor__card hero-decor__card--c" />
  </div>
</template>

<style scoped>
.hero-decor {
  position: absolute;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
}
.hero-decor__glow {
  position: absolute;
  right: -8%;
  top: -30%;
  width: 340px;
  height: 340px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(255, 255, 255, 0.35), transparent 65%);
}
.hero-decor__sphere {
  position: absolute;
  right: 7%;
  top: 18%;
  width: 168px;
  height: 168px;
  border-radius: 50%;
  background:
    radial-gradient(circle at 32% 28%, #fff 0 18%, transparent 19%),
    radial-gradient(circle at 50% 46%, #93c5fd 0 36%, #4f46e5 70%, #1e1b4b 100%);
  box-shadow: 0 24px 50px rgba(15, 23, 42, 0.22);
  transform: translate3d(calc(var(--px, 0) * 18px), calc(var(--py, 0) * 12px), 0);
  transition: transform 160ms linear;
}
.hero-decor__card {
  position: absolute;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.22);
  border: 1px solid rgba(255, 255, 255, 0.45);
  backdrop-filter: blur(10px);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.12);
  transition: transform 160ms linear;
}
.hero-decor__card--a {
  width: 78px;
  height: 58px;
  right: 28%;
  top: 22%;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  transform: translate3d(calc(var(--px, 0) * -14px), calc(var(--py, 0) * 10px), 0);
}
.hero-decor__card--a span {
  display: block;
  height: 6px;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.7);
}
.hero-decor__card--a span:last-child {
  width: 60%;
}
.hero-decor__card--b {
  width: 54px;
  height: 54px;
  right: 8%;
  bottom: 28%;
  background: linear-gradient(145deg, rgba(255, 255, 255, 0.4), rgba(255, 255, 255, 0.08));
  transform: translate3d(calc(var(--px, 0) * 16px), calc(var(--py, 0) * -10px), 0);
}
.hero-decor__card--c {
  width: 48px;
  height: 48px;
  right: 22%;
  bottom: 18%;
  transform: translate3d(calc(var(--px, 0) * 8px), calc(var(--py, 0) * 16px), 0);
}

@media (max-width: 900px) {
  .hero-decor {
    opacity: 0.35;
  }
}
</style>
