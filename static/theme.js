// Bouton de thème à trois états : auto (suit le système), clair, sombre.
// Le choix est mémorisé dans le navigateur ; base.html l'applique avant le premier affichage.
(function () {
  var button = document.getElementById("theme");
  if (!button) return;
  var order = ["auto", "light", "dark"];
  var labels = { auto: "Thème : auto", light: "Thème : clair", dark: "Thème : sombre" };

  function current() {
    var value = document.documentElement.dataset.theme;
    return value === "light" || value === "dark" ? value : "auto";
  }

  function apply(theme) {
    if (theme === "auto") {
      delete document.documentElement.dataset.theme;
    } else {
      document.documentElement.dataset.theme = theme;
    }
    // Le stockage peut être bloqué (navigation privée) : le thème vaut alors pour la page seulement.
    try {
      if (theme === "auto") localStorage.removeItem("theme");
      else localStorage.setItem("theme", theme);
    } catch (e) {}
    button.textContent = labels[theme];
  }

  button.textContent = labels[current()];
  button.addEventListener("click", function () {
    apply(order[(order.indexOf(current()) + 1) % order.length]);
  });
})();
