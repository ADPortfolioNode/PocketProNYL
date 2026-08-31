(function () {
  const pages = [
    { href: "index.html", label: "Home" },
    { href: "demo.html", label: "Concierge" },
    { href: "features.html", label: "Games" },
    { href: "how-it-works.html", label: "How it works" },
    { href: "docs.html", label: "Docs" },
  ];
  const here = (location.pathname.split("/").pop() || "index.html").toLowerCase();
  const nav = pages
    .map((p) => {
      const current = p.href === here || (here === "" && p.href === "index.html");
      return `<a href="${p.href}"${current ? ' aria-current="page"' : ""}>${p.label}</a>`;
    })
    .join("");
  document.querySelectorAll("[data-nav]").forEach((el) => {
    el.outerHTML = `<header class="site-header"><div class="site-header__inner">
      <a class="brand" href="index.html">Pocket<span>Pro</span>:NYL</a>
      <button class="nav-toggle" type="button" aria-label="Open menu" aria-expanded="false"></button>
      <nav class="site-nav" id="site-nav">${nav}</nav>
    </div></header>`;
  });
  document.querySelectorAll("[data-footer]").forEach((el) => {
    el.outerHTML = `<footer class="site-footer"><div class="site-footer__inner">
      <a class="brand" href="index.html" style="color:#dbe9ff">Pocket<span>Pro</span>:NYL</a>
      <p>New York Lottery dashboard — ingest, train, and ask Concierge about draws.</p>
    </div></footer>`;
  });
  document.querySelectorAll(".nav-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const open = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", String(!open));
      document.getElementById("site-nav")?.classList.toggle("is-open", !open);
    });
  });
})();
