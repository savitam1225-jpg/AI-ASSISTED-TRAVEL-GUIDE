// Handles the copilot chat box on the results page.
// WHY fetch() + JSON instead of a full page reload: the copilot should
// feel like a chat, not a form submission - each question appends to
// the log without refreshing the whole plan below it.

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("copilot-form");
  if (!form) return; // copilot box isn't on this page

  const input = document.getElementById("copilot-input");
  const log = document.getElementById("copilot-log");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) return;

    appendMessage("q", question);
    input.value = "";

    try {
      const response = await fetch("/copilot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const data = await response.json();
      appendMessage("a", data.answer);
    } catch (err) {
      appendMessage("a", "Something went wrong reaching the copilot - try again.");
    }
  });

  function appendMessage(cssClass, text) {
    const p = document.createElement("p");
    p.className = cssClass;
    p.textContent = (cssClass === "q" ? "You: " : "Copilot: ") + text;
    log.appendChild(p);
    log.scrollTop = log.scrollHeight;
  }
});
