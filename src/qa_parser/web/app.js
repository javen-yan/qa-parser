"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const token = document.querySelector('meta[name="qa-token"]').content;
  let state = { busy: false, files: [], batch: null, output_directory: "" };
  let acting = false;
  let polling = false;
  let lastList = "";
  let resultFilter = "all";
  let connectionLost = false;
  let stopped = false;
  let generation = 0;
  let pollTimer;
  let folderPath = "";
  let folderParent = null;
  let folderLoading = false;
  let rememberedMode = null;
  const modeStorageKey = "qa-parser.selected-mode";
  const labels = { pending: "等待处理", processing: "正在处理", success: "成功", partial_failure: "部分失败 · 结果不完整", failure: "失败" };

  function showError(message) {
    $("error").textContent = message || "";
    $("error").hidden = !message;
  }
  async function request(path, options = {}) {
    const headers = { "X-QA-Token": token, ...(options.headers || {}) };
    let response;
    try { response = await fetch(path, { ...options, headers, cache: "no-store" }); }
    catch { throw new Error("无法连接转换工具，请确认工具仍在运行。"); }
    let data;
    try { data = await response.json(); }
    catch { throw new Error("未能读取处理结果，请稍后重试。"); }
    if (!response.ok) throw new Error(data.error || "操作未完成，请稍后重试。");
    return data;
  }
  function post(path, value = {}) {
    return request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
  }
  function bytes(size) {
    if (!Number.isFinite(size)) return "Word 文档";
    return size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`;
  }
  function element(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function resultButton(text, callback, disabled = false) {
    const button = element("button", "button text-button", text);
    button.type = "button";
    button.disabled = disabled;
    button.addEventListener("click", callback);
    return button;
  }
  function renderFiles(locked) {
    const files = state.files || [];
    const results = state.batch?.files || [];
    const signature = JSON.stringify([files, results, locked, resultFilter]);
    if (signature === lastList) return;
    lastList = signature;
    const fragment = document.createDocumentFragment();
    files.forEach((file, index) => {
      const result = results.find((entry) => entry.id && entry.id === file.id) || results[index];
      const status = result?.status || "pending";
      if (resultFilter === "attention" && !["partial_failure", "failure"].includes(status)) return;
      const row = element("li", "file-row");
      const nameWrap = element("div", "file-name-wrap");
      nameWrap.append(element("span", "file-symbol", "DOC"));
      const nameText = element("div");
      const detectedKind = result?.kind || result?.detected_kind || result?.classification?.kind;
      const kindLabel = detectedKind === "choice" ? "选择题" : detectedKind === "blank" ? "填空 / 考点" : "";
      const filename = element("p", "file-name", file.name);
      filename.title = file.name;
      nameText.append(filename, element("span", "file-size", `${bytes(file.size)}${kindLabel ? ` · ${kindLabel}` : ""}`));
      nameWrap.append(nameText);
      const statusWrap = element("div", "file-state");
      const statusLine = element("div", `status-line status-${Object.hasOwn(labels, status) ? status : "pending"}`);
      const dot = element("span", "status-dot");
      dot.setAttribute("aria-hidden", "true");
      statusLine.append(dot, document.createTextNode(labels[status] || "等待处理"));
      statusWrap.append(statusLine);
      if (result && ["success", "partial_failure", "failure"].includes(status)) {
        const total = Number(result.total) || 0;
        const exported = Number(result.exported) || 0;
        const omitted = Number(result.omitted) || 0;
        if (total || exported || omitted) statusWrap.append(element("p", "file-detail", `已导出 ${exported} 题 · 未导出 ${omitted} 题`));
        if (status !== "success") {
          const reasons = Array.isArray(result.reasons) ? [...new Set(result.reasons.filter((reason) => typeof reason === "string" && reason.trim()))] : [];
          const detail = reasons.length ? reasons.join("；") : result.message;
          if (detail) statusWrap.append(element("p", "file-detail", detail));
        }
      } else if (status === "processing") statusWrap.append(element("p", "file-detail", "正在识别、转换并检查内容…"));
      const actions = element("div", "file-buttons");
      if (result && ["success", "partial_failure", "failure"].includes(status)) {
        if (result.output && Number(result.exported) > 0) actions.append(resultButton("预览题目", () => openProblems(index, true)));
        if (Number(result.omitted) > 0 || status === "failure") actions.append(resultButton(Number(result.omitted) > 0 ? `未导出题目 · ${result.omitted}` : "为什么未导出", () => openProblems(index)));
        if (result.report) actions.append(resultButton("查看说明", () => openResult("report", index)));
      } else if (!state.batch) {
        const remove = resultButton("移除", () => act(async () => { state = await post("/api/remove", { id: file.id }); }), locked);
        remove.setAttribute("aria-label", `移除 ${file.name}`);
        actions.append(remove);
      }
      row.append(nameWrap, statusWrap, actions);
      fragment.append(row);
    });
    $("filter-empty").hidden = fragment.childNodes.length > 0;
    $("file-list").replaceChildren(fragment);
  }
  function modeLabel(mode) {
    return { auto: "自动识别", choice: "选择题", blank: "填空 / 考点" }[mode] || "自动识别";
  }
  function render() {
    if (stopped) return;
    const locked = acting || state.busy;
    const count = (state.files || []).length;
    const batch = state.batch;
    const finished = Boolean(batch) && !state.busy;
    $("file-count").textContent = `${count} 份`;
    $("files-title").textContent = finished ? "文件结果" : "文档";
    $("drop-zone").hidden = count > 0;
    $("file-list-wrap").hidden = count === 0;
    $("clear").hidden = count === 0 || Boolean(batch);
    $("clear").disabled = locked;
    $("shutdown").disabled = locked;
    for (const id of ["add", "empty-add", "file-input", "choose-output", "modes"]) $(id).disabled = locked || Boolean(batch);
    $("add").hidden = Boolean(batch) || count === 0;
    $("start").hidden = finished;
    $("start").disabled = locked || count === 0 || Boolean(batch) || connectionLost;
    $("start").textContent = state.busy ? "正在转换…" : count ? `开始转换 · ${count} 份` : "开始转换";
    $("continue").hidden = !finished;
    $("continue").disabled = locked;
    $("open-batch").hidden = !finished;
    $("open-batch").disabled = acting || !batch?.delivery_directory;
    $("open-diagnostics").hidden = !finished;
    $("open-diagnostics").disabled = acting || !batch?.output;
    $("open-batch").textContent = finished && !Number(batch.passed) ? "查看失败说明" : "打开结果包";
    $("setup-fields").hidden = finished;
    $("completed-mode").hidden = !finished;
    $("completed-mode-value").textContent = modeLabel(batch?.mode);
    $("action-note").textContent = finished ? "已有结果会保留" : state.busy ? "正在处理，请保持工具运行" : count ? `已添加 ${count} 份文档` : "添加文件后即可转换";
    const path = state.output_directory || "";
    const name = path.split(/[\\/]/).filter(Boolean).pop() || "默认结果文件夹";
    $("output-path").textContent = name;
    $("output-path").title = path;
    $("output-full-path").textContent = path;
    $("choose-output").hidden = Boolean(batch);
    $("mode-note").hidden = Boolean(batch);
    $("file-list").setAttribute("aria-busy", String(state.busy));
    if (batch && ["auto", "choice", "blank"].includes(batch.mode)) {
      const modeInput = document.querySelector(`input[name="mode"][value="${batch.mode}"]`);
      if (modeInput) modeInput.checked = true;
      rememberMode(batch.mode);
      updateModeNote(batch.mode);
    }
    $("batch-summary").hidden = !batch;
    const results = batch?.files || [];
    const attention = results.filter((file) => ["partial_failure", "failure"].includes(file.status)).length;
    $("result-filters").hidden = !finished || count < 2;
    $("filter-all").textContent = `全部文件 ${count}`;
    $("filter-attention").textContent = `有问题 ${attention}`;
    $("filter-all").setAttribute("aria-pressed", String(resultFilter === "all"));
    $("filter-attention").setAttribute("aria-pressed", String(resultFilter === "attention"));
    renderFiles(locked);
    if (batch) {
      const completed = Number(batch.completed) || 0;
      const total = Number(batch.total) || count;
      const status = batch.status || (state.busy ? "processing" : "failure");
      const exported = Number(batch.passed) || 0;
      const omitted = Number(batch.quarantined) || 0;
      const failed = Number(batch.counts?.failure) || results.filter((file) => file.status === "failure").length;
      const unknownFailures = results.filter((file) => file.status === "failure" && !Number(file.total)).length;
      $("batch-summary").className = `batch-summary ${["partial_failure", "failure"].includes(status) ? status : ""}`;
      $("summary-symbol").textContent = state.busy ? "↻" : status === "success" ? "✓" : "!";
      $("summary-title").textContent = state.busy ? "正在转换文档" : status === "success" ? "全部转换完成" : status === "partial_failure" ? "转换结束，结果不完整" : "转换未成功";
      $("progress-text").textContent = `${completed} / ${total} 份已处理`;
      $("progress").hidden = finished;
      $("progress").max = Math.max(total, 1);
      $("progress").value = completed;
      $("summary-counts").hidden = !finished;
      $("exported-count").textContent = exported;
      $("omitted-count").textContent = omitted;
      $("failed-count").textContent = failed;
      $("failed-stat").hidden = failed === 0;
      $("omitted-stat").hidden = omitted === 0 && failed > 0;
      $("summary-message").textContent = state.busy ? "正在转换…" : status === "success" ? "结果包已生成。" : status === "partial_failure" ? `结果不完整，请查看未导出题目。${unknownFailures ? `另有 ${unknownFailures} 份文件无法读取或识别，题数未确认。` : ""}` : batch.message || "未生成题库，请查看失败原因。";
    }
  }
  function updateModeNote(mode) {
    $("mode-note").textContent = mode === "auto" ? "支持混合添加选择题和填空题文档。" : "题型不符的文件将停止转换。";
  }
  function rememberMode(mode) {
    if (!["auto", "choice", "blank"].includes(mode) || mode === rememberedMode) return;
    rememberedMode = mode;
    try { window.sessionStorage.setItem(modeStorageKey, mode); }
    catch { /* Conversion still works when browser storage is unavailable. */ }
  }
  async function act(action) {
    if (acting || state.busy) return;
    acting = true;
    generation++;
    showError("");
    render();
    try { await action(); }
    catch (error) { showError(error.message || "操作未完成，请稍后重试。"); }
    finally { acting = false; render(); }
  }
  async function openResult(target, index) {
    try {
      if (target === "report") {
        const report = await request(`/api/report?index=${encodeURIComponent(index)}`);
        reportIndex = index;
        $("report-name").textContent = report.name || "";
        $("report-text").textContent = report.text || "暂无结果说明。";
        $("report-text").scrollTop = 0;
        if (!$("report-dialog").open) $("report-dialog").showModal();
      } else {
        await post("/api/open", { target, ...(index === undefined ? {} : { index }) });
      }
    } catch (error) { showError(error.message); }
  }
  $("close-report").addEventListener("click", () => $("report-dialog").close());
  let reportIndex = null;
  let problemFileIndex = null;
  let problemData = null;
  let problemRequest = 0;
  const asArray = (value) => Array.isArray(value) ? value : [];

  function describeProblem(problem, index) {
    const section = element("section", "problem-explanation");
    section.id = `problem-reason-${index}`;
    section.tabIndex = -1;
    const head = element("div", "problem-explanation-heading");
    head.append(element("span", "problem-number", String(index + 1)), element("h4", "", problem.label || "此处无法可靠转换"));
    section.append(head);
    const category = [problem.category_label, problem.field_label].filter(Boolean).join(" · ");
    if (category) section.append(element("p", "problem-category", category));
    if (problem.impact) section.append(element("p", "problem-impact", `影响：${problem.impact}`));
    const diagnostics = element("details", "problem-diagnostics");
    diagnostics.append(element("summary", "", "查看定位与诊断"));
    if (problem.reason) diagnostics.append(element("p", "", problem.reason));
    if (problem.source_label) diagnostics.append(element("p", "", problem.source_label));
    const detail = { code: problem.code, source: problem.source, diagnostic: problem.diagnostic };
    diagnostics.append(element("pre", "", JSON.stringify(detail, null, 2)));
    section.append(diagnostics);
    return section;
  }

  function formulaNode(latex) {
    const wrap = element("span", "problem-formula");
    const rendered = element("span", "problem-formula-rendered");
    let available = false;
    if (window.QaMathPreview && typeof latex === "string") {
      try {
        window.QaMathPreview.render(latex, rendered);
        available = !rendered.querySelector(".katex-error");
      } catch { available = false; }
    }
    if (available) wrap.append(rendered);
    else wrap.append(element("span", "formula-unavailable", "[公式暂时无法显示]"));
    const source = element("details", "formula-source");
    source.append(element("summary", "", "公式详情"), element("code", "", latex || ""));
    wrap.append(source);
    return wrap;
  }

  function renderQuestion(questionIndex) {
    const questions = asArray(problemData?.questions);
    const question = questions[questionIndex];
    if (!question) return;
    $("problem-question-list").querySelectorAll("button").forEach((button, index) => {
      button.setAttribute("aria-current", index === questionIndex ? "true" : "false");
    });
    const detail = $("problem-detail");
    const content = document.createDocumentFragment();
    const title = element("div", "problem-question-heading");
    title.append(element("h3", "", `原第 ${question.number} 题`), element("span", "problem-kind", question.kind_label || modeLabel(question.kind)));
    content.append(title);
    const problems = asArray(question.problems);
    const byId = new Map(problems.map((problem, index) => [problem.id, { problem, index }]));
    const jump = (problemId) => {
      const selected = byId.get(problemId);
      if (!selected) return;
      const target = $(`problem-reason-${selected.index}`);
      target?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      target?.focus({ preventScroll: true });
    };
    content.append(element("p", "problem-reading-hint", problemData?.exported ? `对应 Excel 第 ${question.excel_row} 行` : `本题未导出，发现 ${problems.length} 处问题。`));
    const fields = element("div", "problem-fields");
    asArray(question.fields).forEach((field) => {
      const section = element("section", "problem-field");
      const heading = element("h4", "", field.label || field.key || "题目内容");
      const fieldProblems = asArray(field.problem_ids).filter((id) => byId.has(id));
      if (fieldProblems.length) section.classList.add("has-problem");
      section.append(heading);
      const body = element("div", "problem-field-content");
      const segments = asArray(field.segments);
      if (!segments.length) body.append(element("span", "problem-placeholder", "此字段没有可预览内容。"));
      const marked = new Set();
      segments.forEach((segment) => {
        const ids = asArray(segment.problem_ids).filter((id) => byId.has(id));
        const span = element("span", ids.length ? "problem-segment marked" : "problem-segment");
        if (segment.type === "latex") span.append(formulaNode(segment.latex || segment.text || ""));
        else if (segment.type === "image" && typeof segment.src === "string" && /^data:image\/(?:png|jpeg);base64,[A-Za-z0-9+/=\s]+$/.test(segment.src)) {
          const image = element("img", "problem-image");
          image.src = segment.src;
          image.alt = segment.alt || "本题中的原始图片";
          image.loading = "lazy";
          span.append(image);
        } else span.append(element("span", segment.type === "placeholder" || segment.type === "image" ? "problem-placeholder" : "", segment.text || (segment.type === "image" ? "[图片无法可靠预览]" : "")));
        ids.forEach((id) => {
          marked.add(id);
          const issue = byId.get(id);
          const marker = resultButton(`问题 ${issue.index + 1}`, () => jump(id));
          marker.className = "problem-marker";
          marker.title = issue.problem.label;
          marker.setAttribute("aria-label", `查看问题 ${issue.index + 1}：${issue.problem.label || "转换说明"}`);
          span.append(marker);
        });
        body.append(span);
      });
      section.append(body);
      const otherProblems = fieldProblems.filter((id) => !marked.has(id));
      if (otherProblems.length) {
        const note = element("div", "problem-field-note");
        note.append(element("span", "", "此字段存在问题，无法进一步缩小标记位置："));
        otherProblems.forEach((id) => note.append(resultButton(`问题 ${byId.get(id).index + 1}`, () => jump(id))));
        section.append(note);
      }
      fields.append(section);
    });
    content.append(fields);
    const explanations = element("section", "problem-explanations");
    explanations.append(element("h3", "", "未导出原因"));
    problems.forEach((problem, index) => explanations.append(describeProblem(problem, index)));
    if (!problemData?.exported) content.append(explanations);
    asArray(question.preview_notes).forEach((note) => content.append(element("p", "problem-preview-note", note)));
    detail.replaceChildren(content);
    detail.scrollTop = 0;
  }

  async function openProblems(index, exported = false) {
    $("problems-title").textContent = exported ? "已导出题目" : "未导出题目";
    document.querySelector(".problems-notice").textContent = exported ? "已导出题目 · 只读" : "以下题目未导出。选择题号查看原因。";
    $("problems-open-source").hidden = exported;
    document.querySelector('.problem-navigation').setAttribute('aria-label', exported ? '已导出题号' : '未导出题号');
    $("problem-detail").setAttribute('aria-label', exported ? '题目内容' : '题目内容与原因');
    const currentRequest = ++problemRequest;
    problemFileIndex = index;
    problemData = null;
    $("problems-source").textContent = state.files[index]?.name || "";
    $("problems-source").title = $("problems-source").textContent;
    $("problems-layout").hidden = true;
    $("problems-error").hidden = true;
    $("problems-open-source").disabled = true;
    $("problems-empty").hidden = false;
    $("problems-empty").textContent = "正在加载题目…";
    if (!$("problems-dialog").open) $("problems-dialog").showModal();
    try {
      const data = await request(`/api/${exported ? "export-preview" : "problems"}?index=${encodeURIComponent(index)}`);
      if (currentRequest !== problemRequest || !$("problems-dialog").open) return;
      problemData = data;
      try {
        const fonts = await document.fonts.load('16px "QA Formula Unicode"');
        if (!fonts.length || fonts.some(font => font.status !== "loaded")) throw new Error("font unavailable");
      } catch {
        $("problems-error").textContent = "公式字体加载失败，部分符号可能无法显示。";
        $("problems-error").hidden = false;
      }
      if (currentRequest !== problemRequest || !$("problems-dialog").open) return;
      $("problems-source").textContent = data.source_name || state.files[index]?.name || "";
      $("problems-source").title = $("problems-source").textContent;
      $("problems-open-source").disabled = data.source_available === false;
      const questions = asArray(data.questions);
      $("problems-count").textContent = questions.length ? `${questions.length} 道${exported ? "已导出" : "未导出"}` : asArray(data.document_issues).length ? "暂无题目预览" : "无未导出题目";
      $("problems-empty").hidden = questions.length > 0;
      $("problems-layout").hidden = questions.length === 0;
      const navigation = document.createDocumentFragment();
      questions.forEach((question, questionIndex) => {
        const button = element("button", "problem-question-button", `第 ${question.number} 题`);
        button.type = "button";
        button.append(element("span", "", exported ? `Excel 第 ${question.excel_row} 行` : `${asArray(question.problems).length} 处说明`));
        button.addEventListener("click", () => renderQuestion(questionIndex));
        navigation.append(button);
      });
      $("problem-question-list").replaceChildren(navigation);
      $("problem-question-list").scrollTop = 0;
      if (questions.length) renderQuestion(0);
      else {
        const empty = $("problems-empty");
        empty.replaceChildren(element("p", "", data.empty_message || "此文件没有未导出题目。"));
        asArray(data.document_issues).forEach((problem, issueIndex) => empty.append(describeProblem(problem, issueIndex)));
        if (data.source_available === false) empty.append(element("p", "problem-preview-note", "源文档不可用，无法打开原 Word。请查看本文件结果说明。"));
      }
    } catch (error) {
      if (currentRequest !== problemRequest) return;
      $("problems-empty").textContent = "题目预览加载失败。";
      $("problems-error").textContent = error.message || "无法读取题目预览。";
      $("problems-error").hidden = false;
    }
  }
  $("close-problems").addEventListener("click", () => $("problems-dialog").close());
  $("problems-dialog").addEventListener("close", () => { problemRequest++; });
  $("problems-open-source").addEventListener("click", async () => {
    if (problemFileIndex === null) return;
    try { await post("/api/open", { target: "source", index: problemFileIndex }); }
    catch (error) { $("problems-error").textContent = error.message; $("problems-error").hidden = false; }
  });
  $("report-problems").addEventListener("click", () => {
    if (reportIndex === null) return;
    $("report-dialog").close();
    openProblems(reportIndex);
  });

  async function addFiles(list) {
    if (state.busy || acting || state.batch) return;
    const files = Array.from(list);
    if (!files.length) return;
    await act(async () => {
      const valid = files.filter((file) => /\.docx$/i.test(file.name));
      const skipped = files.filter((file) => !/\.docx$/i.test(file.name)).map((file) => ({ name: file.name, reason: "目前仅支持 .docx 格式的 Word 文档。" }));
      $("upload-status").hidden = false;
      try {
        for (let i = 0; i < valid.length; i++) {
          $("upload-status").textContent = `正在添加 ${i + 1} / ${valid.length} 份文档…`;
          const file = valid[i];
          try {
            state = await request("/api/files", { method: "POST", headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) }, body: file });
            render();
          } catch (error) {
            skipped.push({ name: file.name, reason: error.message || "无法添加此文档，请重新选择文件。" });
          }
        }
        if (skipped.length) showError(`有 ${skipped.length} 个文件未添加，其他可用文档已保留。\n${skipped.map((file) => `${file.name}：${file.reason}`).join("\n")}`);
      } finally { $("upload-status").hidden = true; $("file-input").value = ""; }
    });
  }
  $("add").addEventListener("click", () => $("file-input").click());
  $("empty-add").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", (event) => addFiles(event.target.files));
  function resetBatch() { return act(async () => { state = await post("/api/reset"); resultFilter = "all"; }); }
  $("clear").addEventListener("click", resetBatch);
  $("continue").addEventListener("click", resetBatch);
  $("filter-all").addEventListener("click", () => { resultFilter = "all"; render(); });
  $("filter-attention").addEventListener("click", () => { resultFilter = "attention"; render(); });
  function folderControls(loading) {
    folderLoading = loading;
    $("select-folder").disabled = loading || !folderPath;
    $("folder-up").disabled = loading || !folderParent;
    $("folder-list").setAttribute("aria-busy", String(loading));
    $("folder-dialog").querySelectorAll(".folder-entry,.folder-shortcut").forEach((button) => { button.disabled = loading; });
  }
  async function loadFolders(path) {
    if (folderLoading || state.busy) return;
    folderControls(true);
    $("folder-error").hidden = true;
    try {
      const data = await request(`/api/folders${path ? `?path=${encodeURIComponent(path)}` : ""}`);
      folderPath = data.path;
      folderParent = data.parent;
      $("folder-path").textContent = data.path;
      const folders = document.createDocumentFragment();
      (data.folders || []).forEach((folder) => {
        const row = element("li");
        const button = element("button", "folder-entry", folder.name);
        button.type = "button";
        button.addEventListener("click", () => loadFolders(folder.path));
        row.append(button);
        folders.append(row);
      });
      $("folder-list").replaceChildren(folders);
      $("folder-list").scrollTop = 0;
      $("folder-empty").hidden = Boolean(data.folders?.length);
      const shortcuts = document.createDocumentFragment();
      (data.shortcuts || []).forEach((shortcut) => {
        const button = element("button", "button secondary folder-shortcut", shortcut.name);
        button.type = "button";
        button.addEventListener("click", () => loadFolders(shortcut.path));
        shortcuts.append(button);
      });
      $("folder-shortcuts").replaceChildren(shortcuts);
    } catch (error) {
      $("folder-error").textContent = error.message || "无法读取此文件夹，请选择其他位置。";
      $("folder-error").hidden = false;
    } finally { folderControls(false); }
  }
  $("choose-output").addEventListener("click", () => {
    if (acting || state.busy || state.batch) return;
    folderPath = "";
    folderParent = null;
    $("folder-path").textContent = "正在读取…";
    $("folder-list").replaceChildren();
    $("folder-shortcuts").replaceChildren();
    $("folder-empty").hidden = true;
    $("folder-dialog").showModal();
    loadFolders();
  });
  $("cancel-folder").addEventListener("click", () => $("folder-dialog").close());
  $("folder-up").addEventListener("click", () => { if (folderParent) loadFolders(folderParent); });
  $("select-folder").addEventListener("click", async () => {
    if (folderLoading || !folderPath || state.busy || acting) return;
    folderControls(true);
    $("cancel-folder").disabled = true;
    $("folder-error").hidden = true;
    acting = true;
    generation++;
    try {
      state = await post("/api/output", { path: folderPath });
      $("folder-dialog").close();
    } catch (error) {
      $("folder-error").textContent = error.message || "无法使用此位置，请选择其他文件夹。";
      $("folder-error").hidden = false;
    } finally { acting = false; $("cancel-folder").disabled = false; folderControls(false); render(); }
  });
  $("folder-dialog").addEventListener("cancel", (event) => { if (acting) event.preventDefault(); });
  $("start").addEventListener("click", () => act(async () => {
    const mode = document.querySelector('input[name="mode"]:checked').value;
    state = await post("/api/start", { mode });
  }));
  $("shutdown").addEventListener("click", () => act(async () => {
    await post("/api/shutdown");
    stopped = true;
    window.clearInterval(pollTimer);
    $("error").textContent = "工具已关闭，可关闭此页面。";
    $("error").hidden = false;
    $("error").classList.add("closed-banner");
    document.querySelectorAll("button, input, fieldset").forEach((el) => { el.disabled = true; });
  }));
  $("open-diagnostics").addEventListener("click", () => openResult("diagnostics"));
  $("open-batch").addEventListener("click", () => openResult("batch"));
  $("modes").addEventListener("change", () => {
    const mode = document.querySelector('input[name="mode"]:checked').value;
    rememberMode(mode);
    updateModeNote(mode);
  });
  let dragDepth = 0;
  document.querySelector(".files-section").addEventListener("dragenter", (event) => {
    event.preventDefault();
    if (!state.busy && !acting && !state.batch) { dragDepth++; document.querySelector(".files-section").classList.add("drag-over"); $("drop-zone").classList.add("drag-over"); }
  });
  document.querySelector(".files-section").addEventListener("dragover", (event) => event.preventDefault());
  document.querySelector(".files-section").addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) { $("drop-zone").classList.remove("drag-over"); document.querySelector(".files-section").classList.remove("drag-over"); }
  });
  document.querySelector(".files-section").addEventListener("drop", (event) => {
    event.preventDefault(); dragDepth = 0; $("drop-zone").classList.remove("drag-over"); document.querySelector(".files-section").classList.remove("drag-over");
    addFiles(event.dataTransfer.files);
  });
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => event.preventDefault());
  async function poll() {
    if (polling || acting || stopped) return;
    polling = true;
    const before = generation;
    try {
      const next = await request("/api/state");
      if (acting || stopped || before !== generation) return;
      state = next;
      if (connectionLost) { showError(""); connectionLost = false; }
      render();
    } catch {
      if (stopped || before !== generation) return;
      connectionLost = true;
      showError("暂时无法连接转换工具。请确认工具仍在运行；连接恢复后会自动更新进度。");
      $("start").disabled = true;
    } finally { polling = false; }
  }
  try {
    const savedMode = window.sessionStorage.getItem(modeStorageKey);
    if (["auto", "choice", "blank"].includes(savedMode)) {
      rememberedMode = savedMode;
      document.querySelector(`input[name="mode"][value="${savedMode}"]`).checked = true;
      updateModeNote(savedMode);
    }
  } catch { /* Keep the default mode when browser storage is unavailable. */ }
  poll();
  pollTimer = window.setInterval(poll, 1000);
})();
