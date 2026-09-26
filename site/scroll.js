// Scroll effects: reveal-on-scroll, the hero drifting away, the screenshot
// tilting upright, and the "colours as layers" walkthrough.
(() => {
  "use strict";

  const root = document.documentElement;
  const reduceMotion =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  root.classList.add("js");

  // --- Top bar gains a shadow once the page has scrolled. ---
  const topbar = document.querySelector(".topbar");

  // --- Reveal on scroll. Lists reveal their items one after another. ---
  document.querySelectorAll(".release").forEach((el) => el.setAttribute("data-reveal", ""));
  document.querySelectorAll("[data-reveal-stagger]").forEach((group) => {
    [...group.children].forEach((child, i) => {
      child.setAttribute("data-reveal", "");
      child.style.setProperty("--i", i);
    });
  });
  const revealTargets = document.querySelectorAll("[data-reveal]");
  if (reduceMotion || !("IntersectionObserver" in window)) {
    revealTargets.forEach((el) => el.classList.add("is-visible"));
  } else {
    const reveal = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          entry.target.classList.add("is-visible");
          reveal.unobserve(entry.target);
        }
      },
      { rootMargin: "0px 0px -10% 0px", threshold: 0.12 },
    );
    revealTargets.forEach((el) => reveal.observe(el));
  }

  // --- "Colours as layers": each point switches the mock to its state. ---
  const mock = document.querySelector(".colormock");
  const steps = [...document.querySelectorAll(".spotlight-points [data-step]")];
  if (mock && steps.length) {
    const setStep = (name) => {
      mock.dataset.step = name;
      steps.forEach((s) => s.classList.toggle("is-active", s.dataset.step === name));
    };
    setStep(steps[0].dataset.step);
    const wide = window.matchMedia("(min-width: 52.01rem)");
    let cycle = 0;
    const stopCycle = () => {
      clearInterval(cycle);
      cycle = 0;
    };
    if ("IntersectionObserver" in window) {
      // Wide screens: the mock is sticky and follows the point being read.
      const byReading = new IntersectionObserver(
        (entries) => {
          if (!wide.matches) return;
          for (const entry of entries) if (entry.isIntersecting) setStep(entry.target.dataset.step);
        },
        { rootMargin: "-45% 0px -45% 0px" },
      );
      steps.forEach((s) => byReading.observe(s));
      // Narrow screens: the mock cycles through the states while it is on screen.
      const byView = new IntersectionObserver(
        (entries) => {
          if (wide.matches || reduceMotion) return;
          for (const entry of entries) {
            if (entry.isIntersecting && !cycle) {
              let i = 0;
              cycle = setInterval(() => {
                i = (i + 1) % steps.length;
                setStep(steps[i].dataset.step);
              }, 2600);
            } else if (!entry.isIntersecting) {
              stopCycle();
            }
          }
        },
        { threshold: 0.4 },
      );
      byView.observe(mock);
      wide.addEventListener("change", stopCycle);
    }
  }

  // --- Scroll-linked values, updated once per frame. ---
  const hero = document.querySelector(".hero");
  const heroInner = document.querySelector(".hero-inner");
  const shot = document.querySelector(".screenshot");
  let ticking = false;
  const clamp = (v) => Math.min(1, Math.max(0, v));

  function update() {
    ticking = false;
    const y = window.scrollY;
    const vh = window.innerHeight;
    if (topbar) topbar.classList.toggle("is-scrolled", y > 8);
    if (reduceMotion) return;
    if (hero && heroInner) {
      const p = clamp(y / (hero.offsetHeight * 0.6));
      heroInner.style.setProperty("--hero-p", p.toFixed(3));
    }
    if (shot) {
      // 0 while the screenshot's top is at the bottom of the viewport,
      // 1 once it has risen to 30% from the top.
      const top = shot.getBoundingClientRect().top;
      const p = clamp((vh - top) / (vh * 0.7));
      shot.style.setProperty("--shot-p", p.toFixed(3));
    }
  }

  const onScroll = () => {
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(update);
    }
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  update();
})();
