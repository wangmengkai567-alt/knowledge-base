<script setup lang="ts">
/** 登录页左侧玻璃卡片堆叠，随鼠标轻微视差旋转。 */
</script>

<template>
  <div class="glass-stack" aria-hidden="true">
    <div class="glass-stack__orb glass-stack__orb--a" />
    <div class="glass-stack__orb glass-stack__orb--b" />
    <div class="glass-stack__card glass-stack__card--back">
      <span class="glass-stack__mini" />
      <span class="glass-stack__line" />
      <span class="glass-stack__line short" />
    </div>
    <div class="glass-stack__card glass-stack__card--mid">
      <span class="glass-stack__nodes" />
    </div>
    <div class="glass-stack__card glass-stack__card--front">
      <span class="glass-stack__logo">Q</span>
    </div>
    <div class="glass-stack__card glass-stack__card--side">
      <span class="glass-stack__doc" />
    </div>
  </div>
</template>

<style scoped>
.glass-stack {
  position: relative;
  width: min(420px, 100%);
  height: 280px;
  perspective: 900px;
  transform-style: preserve-3d;
  transform: rotateX(calc(var(--py, 0) * -10deg)) rotateY(calc(var(--px, 0) * 14deg));
  transition: transform 160ms linear;
}
.glass-stack__orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(1px);
  pointer-events: none;
}
.glass-stack__orb--a {
  width: 88px;
  height: 88px;
  right: 12%;
  top: 8%;
  background: radial-gradient(circle at 30% 30%, #fff, rgba(165, 180, 252, 0.55) 45%, rgba(99, 102, 241, 0.08) 70%);
  transform: translate3d(calc(var(--px, 0) * -18px), calc(var(--py, 0) * -10px), 40px);
}
.glass-stack__orb--b {
  width: 42px;
  height: 42px;
  left: 18%;
  bottom: 18%;
  background: radial-gradient(circle at 30% 30%, #fff, rgba(56, 189, 248, 0.5) 50%, transparent 72%);
  transform: translate3d(calc(var(--px, 0) * 22px), calc(var(--py, 0) * 14px), 20px);
}
.glass-stack__card {
  position: absolute;
  border-radius: 22px;
  background: linear-gradient(145deg, rgba(255, 255, 255, 0.62), rgba(255, 255, 255, 0.18));
  border: 1px solid rgba(255, 255, 255, 0.7);
  box-shadow:
    0 18px 40px rgba(79, 70, 229, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(16px);
  transform-style: preserve-3d;
}
.glass-stack__card--front {
  width: 148px;
  height: 148px;
  left: 36%;
  top: 42px;
  display: grid;
  place-items: center;
  transform: translate3d(calc(var(--px, 0) * 16px), calc(var(--py, 0) * 12px), 60px);
}
.glass-stack__card--back {
  width: 120px;
  height: 120px;
  left: 14%;
  top: 88px;
  padding: 16px;
  transform: translate3d(calc(var(--px, 0) * -12px), calc(var(--py, 0) * 8px), 10px) rotate(-12deg);
}
.glass-stack__card--mid {
  width: 110px;
  height: 110px;
  right: 10%;
  bottom: 36px;
  transform: translate3d(calc(var(--px, 0) * 10px), calc(var(--py, 0) * -8px), 24px) rotate(14deg);
}
.glass-stack__card--side {
  width: 96px;
  height: 96px;
  left: 28%;
  bottom: 8px;
  transform: translate3d(calc(var(--px, 0) * -8px), calc(var(--py, 0) * 14px), 32px) rotate(-8deg);
}
.glass-stack__logo {
  display: grid;
  place-items: center;
  width: 72px;
  height: 72px;
  border-radius: 20px;
  background: linear-gradient(145deg, #818cf8, #4f46e5);
  color: #fff;
  font-size: 32px;
  font-weight: 700;
  box-shadow: 0 12px 24px rgba(79, 70, 229, 0.35);
}
.glass-stack__mini {
  display: block;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: linear-gradient(145deg, #93c5fd, #6366f1);
  margin-bottom: 10px;
}
.glass-stack__line {
  display: block;
  height: 6px;
  border-radius: 99px;
  background: rgba(99, 102, 241, 0.18);
  margin-top: 8px;
}
.glass-stack__line.short {
  width: 55%;
}
.glass-stack__nodes {
  display: block;
  width: 100%;
  height: 100%;
  background:
    radial-gradient(circle at 30% 35%, #6366f1 0 6px, transparent 7px),
    radial-gradient(circle at 70% 60%, #38bdf8 0 5px, transparent 6px),
    linear-gradient(120deg, transparent 40%, rgba(99, 102, 241, 0.35) 40% 42%, transparent 42%);
}
.glass-stack__doc {
  display: block;
  width: 42px;
  height: 52px;
  margin: 20px auto;
  border-radius: 8px;
  background: linear-gradient(#fff, #e0e7ff);
  box-shadow: inset 0 0 0 1px rgba(99, 102, 241, 0.2);
}

@media (prefers-reduced-motion: reduce) {
  .glass-stack,
  .glass-stack__card,
  .glass-stack__orb {
    transform: none !important;
    transition: none !important;
  }
}
</style>
