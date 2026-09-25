let currentThreadId = localStorage.getItem("travel_thread_id") || null;
let latestAnswerMarkdown = "";
let waitingForApproval = false;
let currentResponseData = null;

const AGENT_META = {
  flight_agent: { label: "Flight Agent", icon: "✈️", color: "#3b82f6", desc: "Aviation & Route Intelligence" },
  hotel_agent: { label: "Hotel Agent", icon: "🏨", color: "#8b5cf6", desc: "Tavily Live Stay Search" },
  weather_agent: { label: "Weather Agent", icon: "🌦️", color: "#06b6d4", desc: "OpenWeather Forecast Check" },
  budget_agent: { label: "Budget Agent", icon: "💰", color: "#10b981", desc: "Cost & Feasibility Analysis" },
  itinerary_agent: { label: "Itinerary Agent", icon: "🗓️", color: "#f59e0b", desc: "Plan Integration & Draft Synthesis" }
};

document.addEventListener("DOMContentLoaded", () => {
  updateThreadBadge();
  updateCharCount();
});

function updateThreadBadge() {
  const badgeText = document.getElementById("threadBadgeText");
  const threadBadge = document.getElementById("threadBadge");
  if (currentThreadId) {
    const shortId = currentThreadId.length > 18 ? currentThreadId.substring(0, 18) + "..." : currentThreadId;
    badgeText.textContent = `Thread: ${shortId}`;
    threadBadge.classList.add("active");
  } else {
    badgeText.textContent = "New Session";
    threadBadge.classList.remove("active");
  }
}

function startNewSession() {
  if (confirm("Start a new travel planning session? This will clear current thread context.")) {
    currentThreadId = null;
    localStorage.removeItem("travel_thread_id");
    updateThreadBadge();
    document.getElementById("userInput").value = "";
    updateCharCount();
    document.getElementById("workflowSection").classList.add("hidden");
    document.getElementById("resultSection").classList.add("hidden");
    document.getElementById("approvalSection").classList.add("hidden");
    hideError();
    waitingForApproval = false;
    currentResponseData = null;
  }
}

function updateCharCount() {
  const input = document.getElementById("userInput");
  const countSpan = document.getElementById("charCount");
  if (input && countSpan) {
    countSpan.textContent = `${input.value.length} chars`;
  }
}

function switchInputMode(mode) {
  const tabFreeform = document.getElementById("tabFreeform");
  const tabGuided = document.getElementById("tabGuided");
  const freeformArea = document.getElementById("freeformArea");
  const guidedArea = document.getElementById("guidedArea");

  if (mode === "freeform") {
    tabFreeform.classList.add("active");
    tabGuided.classList.remove("active");
    freeformArea.classList.remove("hidden");
    guidedArea.classList.add("hidden");
  } else {
    tabGuided.classList.add("active");
    tabFreeform.classList.remove("active");
    guidedArea.classList.remove("hidden");
    freeformArea.classList.add("hidden");
  }
}

function applyGuidedForm() {
  const dest = document.getElementById("gDestination").value.trim();
  const orig = document.getElementById("gOrigin").value.trim();
  const dur = document.getElementById("gDuration").value.trim();
  const bud = document.getElementById("gBudget").value.trim();
  const style = document.getElementById("gStyle").value;

  if (!dest) {
    showError("Please specify at least a destination city or country in the guided form.");
    return;
  }

  let promptParts = [`Plan a complete travel itinerary for ${dest}`];
  if (orig) promptParts.push(`departing from ${orig}`);
  if (dur) promptParts.push(`for a duration of ${dur}`);
  if (bud) promptParts.push(`under a budget of ${bud}`);
  if (style) promptParts.push(`with a ${style} travel style`);
  promptParts.push("including flight recommendations, best accommodation choices, local weather advice, and daily sightseeing schedule.");

  const generatedPrompt = promptParts.join(" ") + ".";
  setPrompt(generatedPrompt);
  switchInputMode("freeform");
  sendMessage();
}

function setPrompt(text) {
  const input = document.getElementById("userInput");
  input.value = text;
  updateCharCount();
  input.focus();
}

function appendFeedback(text) {
  const feedbackInput = document.getElementById("approvalFeedback");
  if (feedbackInput.value.trim().length > 0) {
    feedbackInput.value += " " + text;
  } else {
    feedbackInput.value = text;
  }
  feedbackInput.focus();
}

function setLoading(isLoading, mode = "draft") {
  const sendBtn = document.getElementById("sendBtn");
  const btnText = document.getElementById("btnText");
  const btnLoader = document.getElementById("btnLoader");
  const approveBtn = document.getElementById("approveBtn");
  const reviseBtn = document.getElementById("reviseBtn");
  const skeletonLoader = document.getElementById("skeletonLoader");

  sendBtn.disabled = isLoading;
  approveBtn.disabled = isLoading;
  reviseBtn.disabled = isLoading;

  if (isLoading) {
    skeletonLoader.classList.remove("hidden");
    if (mode === "draft") {
      btnText.classList.add("hidden");
      btnLoader.classList.remove("hidden");
    }
  } else {
    skeletonLoader.classList.add("hidden");
    btnText.classList.remove("hidden");
    btnLoader.classList.add("hidden");
  }
}

function showError(message) {
  const errorBox = document.getElementById("errorBox");
  errorBox.innerHTML = `
    <div class="error-content">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      <span>${escapeHtml(message)}</span>
    </div>
  `;
  errorBox.classList.remove("hidden");
  errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
}

function hideError() {
  const errorBox = document.getElementById("errorBox");
  errorBox.classList.add("hidden");
  errorBox.innerHTML = "";
}

function escapeHtml(text) {
  if (!text) return "";
  return text.replace(/[&<>"']/g, function(m) {
    return {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    }[m];
  });
}

function renderMarkdown(element, markdown) {
  if (!element) return;
  if (typeof marked !== "undefined") {
    element.innerHTML = marked.parse(markdown || "");
  } else {
    element.innerText = markdown || "";
  }
}

function showWorkflow(data) {
  const section = document.getElementById("workflowSection");
  const reasoning = document.getElementById("supervisorReasoning");
  const chips = document.getElementById("agentChips");
  const guardrailBadge = document.getElementById("guardrailBadge");

  reasoning.textContent = data.supervisor_reasoning || "Supervisor analyzed request and selected optimal agent execution path.";
  chips.innerHTML = "";

  const selected = data.selected_agents || [];
  if (selected.length === 0) {
    chips.innerHTML = `<span class="empty-agent-msg">No agents required. Request handled by input guardrail.</span>`;
  } else {
    selected.forEach((agentKey, index) => {
      const meta = AGENT_META[agentKey] || { label: agentKey, icon: "🤖", color: "#64748b", desc: "Specialist Agent" };
      const card = document.createElement("div");
      card.className = "agent-card active";
      card.style.animationDelay = `${index * 0.1}s`;
      card.innerHTML = `
        <div class="agent-card-header">
          <span class="agent-icon" style="background:${meta.color}20; border-color:${meta.color}40">${meta.icon}</span>
          <span class="agent-title">${meta.label}</span>
          <span class="agent-status-badge">Active</span>
        </div>
        <div class="agent-card-desc">${meta.desc}</div>
      `;
      chips.appendChild(card);
    });
  }

  if (data.guardrail_allowed === false) {
    guardrailBadge.textContent = "Guardrail Blocked";
    guardrailBadge.className = "guardrail-badge blocked";
  } else {
    guardrailBadge.textContent = "Guardrail Passed";
    guardrailBadge.className = "guardrail-badge passed";
  }

  section.classList.remove("hidden");
}

function showResult(answer, threadId, isDraft = false, data = {}) {
  latestAnswerMarkdown = answer || "";
  currentResponseData = data;

  const resultSection = document.getElementById("resultSection");
  const resultBox = document.getElementById("resultBox");
  const threadInfo = document.getElementById("threadInfo");
  const resultTitle = document.getElementById("resultTitle");
  const planStatusTag = document.getElementById("planStatusTag");

  renderMarkdown(resultBox, latestAnswerMarkdown);
  threadInfo.textContent = `Thread ID: ${threadId}`;
  
  if (isDraft) {
    resultTitle.textContent = "Draft Travel Plan";
    planStatusTag.textContent = "Awaiting HITL Review";
    planStatusTag.className = "status-tag draft";
  } else {
    resultTitle.textContent = "Your AI Travel Plan";
    planStatusTag.textContent = "Final Verified Plan";
    planStatusTag.className = "status-tag final";
  }

  // Populate sub-tabs if data exists
  setupResultSubTabs(data);

  resultSection.classList.remove("hidden");
  resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function setupResultSubTabs(data) {
  const flightBtn = document.getElementById("tabFlightBtn");
  const hotelBtn = document.getElementById("tabHotelBtn");
  const weatherBtn = document.getElementById("tabWeatherBtn");
  const budgetBtn = document.getElementById("tabBudgetBtn");

  const flightBox = document.getElementById("flightBox");
  const hotelBox = document.getElementById("hotelBox");
  const weatherBox = document.getElementById("weatherBox");
  const budgetBox = document.getElementById("budgetBox");

  // Flight tab
  if (data.flight_results && data.flight_results.trim()) {
    renderMarkdown(flightBox, data.flight_results);
    flightBtn.classList.remove("hidden");
  } else {
    flightBtn.classList.add("hidden");
  }

  // Hotel tab
  if (data.hotel_results && data.hotel_results.trim()) {
    renderMarkdown(hotelBox, data.hotel_results);
    hotelBtn.classList.remove("hidden");
  } else {
    hotelBtn.classList.add("hidden");
  }

  // Weather tab
  if (data.weather_results && data.weather_results.trim()) {
    renderMarkdown(weatherBox, data.weather_results);
    weatherBtn.classList.remove("hidden");
  } else {
    weatherBtn.classList.add("hidden");
  }

  // Budget tab
  if (data.budget_results && data.budget_results.trim()) {
    renderMarkdown(budgetBox, data.budget_results);
    budgetBtn.classList.remove("hidden");
  } else {
    budgetBtn.classList.add("hidden");
  }
}

function switchResultTab(tabName, btnElement) {
  // Reset tab buttons
  document.querySelectorAll(".res-tab").forEach(btn => btn.classList.remove("active"));
  btnElement.classList.add("active");

  // Reset tab panes
  document.querySelectorAll(".tab-pane").forEach(pane => pane.classList.add("hidden"));

  if (tabName === "fullPlan") {
    document.getElementById("tabFullPlan").classList.remove("hidden");
  } else if (tabName === "flightTab") {
    document.getElementById("tabFlight").classList.remove("hidden");
  } else if (tabName === "hotelTab") {
    document.getElementById("tabHotel").classList.remove("hidden");
  } else if (tabName === "weatherTab") {
    document.getElementById("tabWeather").classList.remove("hidden");
  } else if (tabName === "budgetTab") {
    document.getElementById("tabBudget").classList.remove("hidden");
  }
}

function showApproval(data) {
  waitingForApproval = true;
  const section = document.getElementById("approvalSection");
  const approvalRequest = document.getElementById("approvalRequest");
  approvalRequest.textContent = data.approval_request ||
    "Please review the generated draft itinerary. Approve it to create the final plan, or provide feedback for revision.";
  section.classList.remove("hidden");
  section.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function hideApproval() {
  waitingForApproval = false;
  document.getElementById("approvalSection").classList.add("hidden");
  document.getElementById("approvalFeedback").value = "";
}

async function sendMessage() {
  hideError();

  if (waitingForApproval) {
    showError("Please approve or revise the current draft itinerary before submitting a new travel request.");
    return;
  }

  const input = document.getElementById("userInput");
  const message = input.value.trim();

  if (!message) {
    showError("Please enter your travel request first.");
    input.focus();
    return;
  }

  setLoading(true, "draft");

  try {
    const response = await fetch("/api/travel", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        message: message,
        thread_id: currentThreadId
      })
    });

    const data = await response.json();

    if (!response.ok || !data.success) {
      throw new Error(data.error || "Something went wrong processing your travel request.");
    }

    currentThreadId = data.thread_id;
    localStorage.setItem("travel_thread_id", currentThreadId);
    updateThreadBadge();

    showWorkflow(data);

    if (data.requires_approval) {
      showResult(data.itinerary || data.answer, data.thread_id, true, data);
      showApproval(data);
    } else {
      hideApproval();
      showResult(data.answer, data.thread_id, false, data);
    }
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false, "draft");
  }
}

async function submitApproval(approved) {
  hideError();

  if (!currentThreadId || !waitingForApproval) {
    showError("There is no draft waiting for human approval.");
    return;
  }

  const feedbackInput = document.getElementById("approvalFeedback");
  const feedback = feedbackInput.value.trim();

  if (!approved && !feedback) {
    showError("Please provide revision feedback when requesting changes.");
    feedbackInput.focus();
    return;
  }

  setLoading(true, "approval");

  try {
    const response = await fetch("/api/travel/approve", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        thread_id: currentThreadId,
        approved: approved,
        feedback: feedback
      })
    });

    const data = await response.json();

    if (!response.ok || !data.success) {
      throw new Error(data.error || "Could not resume the travel planning workflow.");
    }

    showWorkflow(data);
    hideApproval();
    showResult(data.answer, data.thread_id, false, data);
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false, "approval");
  }
}

function copyResult() {
  const resultBox = document.getElementById("resultBox");
  const text = resultBox.innerText;

  if (!text) {
    showError("Nothing available to copy.");
    return;
  }

  navigator.clipboard.writeText(text)
    .then(() => {
      const copyBtn = document.querySelector(".copy-btn span");
      const originalText = copyBtn.textContent;
      copyBtn.textContent = "Copied!";

      setTimeout(() => {
        copyBtn.textContent = originalText;
      }, 1600);
    })
    .catch(() => {
      showError("Could not copy content to clipboard.");
    });
}

function downloadPDF() {
  const pdfContent = document.getElementById("pdfContent");

  if (!latestAnswerMarkdown || !pdfContent) {
    showError("No travel plan available to export as PDF.");
    return;
  }

  const downloadBtn = document.querySelector(".download-btn span");
  const oldText = downloadBtn.textContent;
  downloadBtn.textContent = "Generating PDF...";

  const options = {
    margin: 0.5,
    filename: `TripMate-Travel-Plan-${Date.now()}.pdf`,
    image: {
      type: "jpeg",
      quality: 0.98
    },
    html2canvas: {
      scale: 2,
      useCORS: true,
      backgroundColor: "#ffffff"
    },
    jsPDF: {
      unit: "in",
      format: "a4",
      orientation: "portrait"
    },
    pagebreak: {
      mode: ["avoid-all", "css", "legacy"]
    }
  };

  html2pdf()
    .set(options)
    .from(pdfContent)
    .save()
    .then(() => {
      downloadBtn.textContent = oldText;
    })
    .catch((err) => {
      downloadBtn.textContent = oldText;
      showError("Failed to generate PDF document: " + err.message);
    });
}

document.addEventListener("keydown", function(event) {
  if (event.ctrlKey && event.key === "Enter") {
    sendMessage();
  }
});