function $(id) {
  return document.getElementById(id);
}

async function callApi(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Request failed");
  }
  return await res.json();
}

function showStudent() {
  $("student-view").classList.remove("hidden");
  $("ta-view").classList.add("hidden");
  $("faq-view").classList.add("hidden");
  $("tab-student").classList.add("active");
  $("tab-ta").classList.remove("active");
  $("tab-faq").classList.remove("active");
}

function showTA() {
  $("student-view").classList.add("hidden");
  $("ta-view").classList.remove("hidden");
  $("faq-view").classList.add("hidden");
  $("tab-student").classList.remove("active");
  $("tab-ta").classList.add("active");
  $("tab-faq").classList.remove("active");
  loadQueue();
  loadCourseDocs();
  loadSettings(); 
}

function showFAQ() {
  $("student-view").classList.add("hidden");
  $("ta-view").classList.add("hidden");
  $("faq-view").classList.remove("hidden");
  $("tab-student").classList.remove("active");
  $("tab-ta").classList.remove("active");
  $("tab-faq").classList.add("active");
  loadFAQ();
}

async function handleStudentSubmit() {
  const name = $("s-name").value.trim();
  const course = $("s-course").value.trim();
  const question = $("s-question").value.trim();
  if (!name || !question) {
    $("s-status").textContent = "Please provide your name and question.";
    return;
  }
  $("s-status").textContent = "Submitting your question and generating suggestions...";
  $("s-ai-answer").classList.add("hidden");
  try {
    const payload = {
      student_name: name,
      course: course || null,
      question_text: question,
    };
    const data = await callApi("/api/questions", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("s-status").textContent =
      "You're in the queue. The TA can now see your question. You can also read the suggested answer below.";
    $("s-ai-text").textContent = data.ai_answer || "(No AI suggestion available.)";
    if (data.ai_sources) {
      $("s-ai-sources").textContent = "Based on: " + data.ai_sources;
    } else {
      $("s-ai-sources").textContent = "";
    }
    $("s-ai-answer").classList.remove("hidden");
  } catch (err) {
    console.error(err);
    $("s-status").textContent = "Error: " + err.message;
  }
}

function renderTATable(queue) {
  const tbody = $("ta-table").querySelector("tbody");
  tbody.innerHTML = "";
  (queue.questions || []).forEach((q) => {
    const tr = document.createElement("tr");
    const shortQuestion =
      q.question_text.length > 80
        ? q.question_text.slice(0, 77) + "..."
        : q.question_text;
    const shortAI =
      q.ai_answer && q.ai_answer.length > 80
        ? q.ai_answer.slice(0, 77) + "..."
        : q.ai_answer || "";
    tr.innerHTML = `
      <td>${q.id}</td>
      <td>${q.student_name}</td>
      <td>${q.course || ""}</td>
      <td title="${q.question_text}">${shortQuestion}</td>
      <td title="${q.ai_answer || ""}">${shortAI}</td>
      <td>${q.status}</td>
      <td>
        <button class="small" data-id="${q.id}" data-action="start">Start</button>
        <button class="small" data-id="${q.id}" data-action="done">Done</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function loadQueue() {
  $("ta-status").textContent = "Loading queue...";
  try {
    const data = await callApi("/api/queue");
    renderTATable(data);
    $("ta-status").textContent =
      data.questions.length === 0 ? "No students in the queue." : "";
  } catch (err) {
    console.error(err);
    $("ta-status").textContent = "Error: " + err.message;
  }
}

async function handleTAButtonClick(event) {
  const target = event.target;
  if (!target.matches("button.small")) return;
  const id = target.getAttribute("data-id");
  const action = target.getAttribute("data-action");
  if (!id || !action) return;
  try {
    if (action === "start") {
      await callApi(`/api/questions/${id}/status`, {
        method: "POST",
        body: JSON.stringify({ status: "in_progress" }),
      });
    } else if (action === "done") {
      const resolved = prompt(
        "Enter a short summary of the answer (this will be used for the FAQ):",
        ""
      );
      if (resolved === null) return; // User clicked cancel, do nothing
      
      const saveToFaq = true;
      
      await callApi(`/api/questions/${id}/resolve`, {
        method: "POST",
        body: JSON.stringify({
          resolved_answer: resolved || "Answered during office hours.",
          save_to_faq: saveToFaq, 
        }),
      });
    }
    await loadQueue();
  } catch (err)
 {
    console.error(err);
    alert("Error: " + err.message);
  }
}

function renderFAQList(faqs) {
  const container = $("faq-list");
  container.innerHTML = "";
  if (!faqs || faqs.length === 0) {
    container.innerHTML = '<p class="status">No FAQ entries found. (Note: Only questions asked multiple times, as set by the TA, will appear here.)</p>';
    return;
  }
  
  const clusteredFaqs = faqs.filter(f => f.cluster_id !== null);
  const unclusteredFaqs = faqs.filter(f => f.cluster_id === null);
  
  let currentCluster = null;
  let clusterCount = 0;
  
  clusteredFaqs.forEach((faq) => {
    if (faq.cluster_id !== currentCluster) {
      currentCluster = faq.cluster_id;
      clusterCount++;
      const header = document.createElement("div");
      header.className = "faq-cluster-header";
      const clusterName = faq.cluster_name || `Topic ${clusterCount}`;
      header.textContent = `${clusterName}`;
      container.appendChild(header);
    }
    
    const item = document.createElement("div");
    item.className = "faq-item";
    const date = new Date(faq.created_at).toLocaleDateString();
    const askCountText = faq.ask_count > 1 ? ` (Asked ${faq.ask_count} times)` : "";
    item.innerHTML = `
      <div class="faq-question">Q: ${faq.question}</div>
      <div class="faq-answer">A: ${faq.answer}</div>
      <div class="faq-meta">Added on ${date}${askCountText}</div>
    `;
    container.appendChild(item);
  });
  
  if (unclusteredFaqs.length > 0) {
     const header = document.createElement("div");
     header.className = "faq-cluster-header";
     header.textContent = (clusteredFaqs.length > 0) ? "Other Questions" : "Questions";
     container.appendChild(header);
  }
  
  unclusteredFaqs.forEach((faq) => {
    const item = document.createElement("div");
    item.className = "faq-item";
    const date = new Date(faq.created_at).toLocaleDateString();
    const askCountText = faq.ask_count > 1 ? ` (Asked ${faq.ask_count} times)` : "";
    item.innerHTML = `
      <div class="faq-question">Q: ${faq.question}</div>
      <div class="faq-answer">A: ${faq.answer}</div>
      <div class="faq-meta">Added on ${date}${askCountText}</div>
    `;
    container.appendChild(item);
  });
}

async function loadFAQ() {
  $("faq-status").textContent = "Loading FAQ...";
  try {
    const data = await callApi("/api/faq");
    renderFAQList(data);
    $("faq-status").textContent = "";
  } catch (err) {
    console.error(err);
    $("faq-status").textContent = "Error: " + err.message;
  }
}

async function handleDocSubmit() {
  const title = $("doc-title").value.trim();
  const type = $("doc-type").value;
  const content = $("doc-content").value.trim();
  
  if (!title || !content) {
    $("doc-status").textContent = "Please provide title and content.";
    return;
  }
  
  $("doc-status").textContent = "Adding course material...";
  try {
    await callApi("/api/course_docs", {
      method: "POST",
      body: JSON.stringify({
        title: title,
        content: content,
        source_type: type,
      }),
    });
    $("doc-status").textContent = "Course material added successfully!";
    $("doc-title").value = "";
    $("doc-content").value = "";
    $("doc-file").value = null; // Clear the file input
    await loadCourseDocs();
  } catch (err) {
    console.error(err);
    $("doc-status").textContent = "Error: " + err.message;
  }
}

function renderCourseDocs(docs) {
  const container = $("doc-list");
  container.innerHTML = "";
  if (!docs || docs.length === 0) {
    container.innerHTML = '<p class="status">No course materials yet.</p>';
    return;
  }
  
  docs.forEach((doc) => {
    const item = document.createElement("div");
    item.className = "doc-item";
    const preview = doc.content.length > 100 ? doc.content.slice(0, 100) + "..." : doc.content;
    item.innerHTML = `
      <div class="doc-info">
        <div class="doc-title">${doc.title}</div>
        <div class="doc-type">Type: ${doc.source_type}</div>
        <div class="doc-preview">${preview}</div>
      </div>
      <button class="small" data-doc-id="${doc.id}" onclick="deleteDoc(${doc.id})">Delete</button>
    `;
    container.appendChild(item);
  });
}

async function loadCourseDocs() {
  try {
    const data = await callApi("/api/course_docs");
    renderCourseDocs(data);
  } catch (err) {
    console.error(err);
  }
}

async function deleteDoc(docId) {
  if (!confirm("Are you sure you want to delete this course material?")) {
    return;
  }
  try {
    await callApi(`/api/course_docs/${docId}`, { method: "DELETE" });
    await loadCourseDocs();
  } catch (err) {
    console.error(err);
    alert("Error deleting document: " + err.message);
  }
}

async function loadSettings() {
    $("setting-status").textContent = "Loading settings...";
    try {
        const settings = await callApi("/api/settings");
        const threshold = settings.find(s => s.key === "faq_threshold");
        if (threshold) {
            $("setting-faq-threshold").value = threshold.value;
        }
        $("setting-status").textContent = "";
    } catch (err) {
        console.error(err);
        $("setting-status").textContent = "Error loading settings.";
    }
}

async function saveSettings() {
    $("setting-status").textContent = "Saving...";
    const threshold = $("setting-faq-threshold").value;
    
    try {
        await callApi("/api/settings", {
            method: "POST",
            body: JSON.stringify({
                key: "faq_threshold",
                value: threshold,
            }),
        });
        $("setting-status").textContent = "Settings saved!";
        if (!$("faq-view").classList.contains("hidden")) {
            await loadFAQ();
        }
    } catch (err) {
        console.error(err);
        $("setting-status").textContent = "Error saving settings.";
    }
}

async function handleDeleteAllFAQs() {
  if (!confirm("Are you sure you want to PERMANENTLY delete ALL FAQ entries? This cannot be undone.")) {
    return;
  }
  
  $("faq-status").textContent = "Deleting all FAQs...";
  try {
    const data = await callApi("/api/faq/all", { method: "DELETE" });
    $("faq-status").textContent = data.message || "All FAQs deleted.";
    await loadFAQ(); 
  } catch (err) {
    console.error(err);
    $("faq-status").textContent = "Error: " + err.message;
  }
}

function handleFileLoad(event) {
  const file = event.target.files[0];
  if (!file) {
    return; // No file selected
  }

  // Auto-fill the title with the file name (e.g., "HW1_Notes")
  const fileName = file.name.split('.').slice(0, -1).join('.');
  $("doc-title").value = fileName;

  // Read the file content
  const reader = new FileReader();
  reader.onload = function(e) {
    const content = e.target.result;
    $("doc-content").value = content; // Put text into the textarea
    $("doc-status").textContent = `Loaded file ${file.name}. Click 'Add' to save.`;
  };
  reader.onerror = function() {
     $("doc-status").textContent = "Error reading file.";
  };
  reader.readAsText(file);
}


window.addEventListener("DOMContentLoaded", () => {
  $("tab-student").addEventListener("click", showStudent);
  $("tab-ta").addEventListener("click", showTA);
  $("tab-faq").addEventListener("click", showFAQ);
  $("s-submit").addEventListener("click", handleStudentSubmit);
  $("ta-refresh").addEventListener("click", loadQueue);
  $("faq-refresh").addEventListener("click", loadFAQ);
  $("ta-table").addEventListener("click", handleTAButtonClick);
  $("doc-submit").addEventListener("click", handleDocSubmit);
  $("doc-refresh").addEventListener("click", loadCourseDocs);
  $("setting-save").addEventListener("click", saveSettings);
  $("faq-delete-all").addEventListener("click", handleDeleteAllFAQs);

  // **** ADDED NEW EVENT LISTENER ****
  $("doc-file").addEventListener("change", handleFileLoad);

  showStudent();
});