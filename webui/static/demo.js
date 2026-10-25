/* === Web-VLMAgent Demo — Live Presentation View === */
(function () {
  const API = '';
  let currentView = 'marked';
  let currentStep = 0;
  let totalSteps = 0;
  let autoPlayTimer = null;
  let pollTimer = null;
  let currentRunPath = null;
  let runData = null;

  // === DOM refs ===
  const $ = (s) => document.querySelector(s);
  const screenStage = $('#screenStage');
  const screenImage = $('#screenImage');
  const placeholder = screenStage.querySelector('.placeholder');
  const statusDot = $('#statusDot');
  const statusText = $('#statusText');
  const taskText = $('#taskText');
  const thoughtText = $('#thoughtText');
  const actionBadge = $('#actionBadge');
  const actionDetail = $('#actionDetail');
  const verifyText = $('#verifyText');
  const metaUrl = $('#metaUrl');
  const metaProvider = $('#metaProvider');
  const metaSteps = $('#metaSteps');
  const metaStatus = $('#metaStatus');
  const stepCount = $('#stepCount');
  const timeline = $('#timeline');
  const playControls = $('#playControls');

  // === Status ===
  function setStatus(s) {
    statusDot.className = 'dot ' + s;
    statusText.textContent = { idle: 'Idle', running: 'Running', success: 'Success', fail: 'Failed' }[s] || s;
  }

  // === Start task ===
  $('#startBtn').addEventListener('click', async () => {
    const url = $('#demoUrl').value.trim();
    const task = $('#demoTask').value.trim();
    const provider = $('#demoProvider').value;
    if (!url || !task) return alert('Please fill URL and task.');
    setStatus('running');
    resetUI();
    taskText.textContent = task;
    metaUrl.textContent = url.length > 60 ? url.slice(0, 57) + '...' : url;
    metaProvider.textContent = provider;
    metaStatus.textContent = 'Starting...';
    metaStatus.dataset.status = 'running';

    try {
      const resp = await fetch(API + '/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, task, provider, max_steps: 8, headless: true }),
      });
      const data = await resp.json();
      if (data.error) { alert(data.error); setStatus('idle'); return; }
      pollJob(data.job.id, provider);
      $('#startBtn').disabled = true;
      $('#startBtn').textContent = 'Running...';
    } catch (e) {
      alert('Failed to start: ' + e.message);
      setStatus('idle');
    }
  });

  // === Poll job ===
  function pollJob(jobId, provider) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const resp = await fetch(API + '/api/job?id=' + jobId);
        const data = await resp.json();
        const job = data.job;
        if (!job) return;

        metaStatus.textContent = job.status;
        metaStatus.dataset.status = job.status === 'completed' ? (job.success ? 'success' : 'failed') : 'running';

        if (job.status === 'completed' || job.status === 'done') {
          clearInterval(pollTimer);
          pollTimer = null;
          if (job.trace_path) {
            currentRunPath = job.trace_path;
            await loadTrace(job.trace_path);
          }
          setStatus(job.success ? 'success' : 'fail');
          $('#startBtn').disabled = false;
          $('#startBtn').textContent = '▶ 启动 Agent';
        } else {
          metaSteps.textContent = 'running...';
        }
      } catch (e) { /* ignore */ }
    }, 1500);
  }

  // === Load trace ===
  async function loadTrace(tracePath) {
    try {
      const resp = await fetch(API + '/api/run?path=' + encodeURIComponent(tracePath));
      runData = await resp.json();
      if (!runData || !runData.run) return;

      const run = runData.run;
      metaSteps.textContent = run.steps_used || '?';
      metaStatus.textContent = run.success ? 'SUCCESS' : 'FAILED';
      metaStatus.dataset.status = run.success ? 'success' : 'failed';
      stepCount.textContent = (run.steps_used || 0) + ' steps';

      // Build timeline
      totalSteps = run.steps_used || 0;
      timeline.innerHTML = '';
      for (let i = 0; i < totalSteps; i++) {
        const dot = document.createElement('div');
        dot.className = 'step-dot';
        dot.textContent = i + 1;
        dot.title = 'Step ' + (i + 1);
        dot.addEventListener('click', () => loadStep(i));
        timeline.appendChild(dot);
      }
      playControls.hidden = false;

      if (totalSteps > 0) loadStep(0);
    } catch (e) {
      console.error('Failed to load trace:', e);
    }
  }

  // === Load step ===
  async function loadStep(index) {
    currentStep = index;
    if (!currentRunPath) return;

    const stepDir = 'step_' + String(index).padStart(3, '0');
    const base = currentRunPath + '/' + stepDir;

    // Update timeline
    const dots = timeline.querySelectorAll('.step-dot');
    dots.forEach((d, i) => {
      d.className = 'step-dot' + (i === index ? ' active' : '') + (i < index ? ' done' : '');
    });

    // Load screenshot
    const imgType = currentView === 'marked' ? 'marked_screenshot.png' : 'screenshot.png';
    screenImage.src = API + '/api/file?path=' + encodeURIComponent(base + '/' + imgType);
    screenImage.hidden = false;
    placeholder.hidden = true;

    // Load action
    try {
      const actionResp = await fetch(API + '/api/text?path=' + encodeURIComponent(base + '/parsed_action.json'));
      const actionText = await actionResp.text();
      const action = JSON.parse(actionText);
      thoughtText.textContent = action.thought || '--';
      actionBadge.textContent = action.action || '--';
      const detailParts = [];
      if (action.target_id != null) detailParts.push('target=' + action.target_id);
      if (action.text) detailParts.push('text="' + action.text.slice(0, 40) + (action.text.length > 40 ? '...' : '') + '"');
      if (action.direction) detailParts.push(action.direction);
      actionDetail.textContent = detailParts.join(' | ') || '--';
    } catch (e) {
      thoughtText.textContent = '--';
      actionBadge.textContent = '?';
    }

    // Load verification
    try {
      const verifyResp = await fetch(API + '/api/text?path=' + encodeURIComponent(base + '/final_verification.json'));
      const verifyData = JSON.parse(await verifyResp.text());
      verifyText.textContent = verifyData.reason || '--';
    } catch (e) {
      verifyText.textContent = '--';
    }
  }

  // === View toggle (SoM / raw) ===
  document.querySelectorAll('.toggle-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentView = btn.dataset.view;
      if (currentRunPath) loadStep(currentStep);
    });
  });

  // === Auto play ===
  let autoPlaying = false;
  $('#autoPlayBtn').addEventListener('click', () => {
    if (autoPlaying) {
      autoPlaying = false;
      if (autoPlayTimer) clearInterval(autoPlayTimer);
      $('#autoPlayBtn').textContent = '▶ 自动播放';
      return;
    }
    autoPlaying = true;
    $('#autoPlayBtn').textContent = '⏸ 停止';
    if (autoPlayTimer) clearInterval(autoPlayTimer);
    autoPlayTimer = setInterval(() => {
      if (currentStep < totalSteps - 1) {
        loadStep(currentStep + 1);
      } else {
        autoPlaying = false;
        if (autoPlayTimer) clearInterval(autoPlayTimer);
        $('#autoPlayBtn').textContent = '⟳ 重播';
      }
    }, 3000);
  });

  function resetUI() {
    currentRunPath = null;
    runData = null;
    totalSteps = 0;
    currentStep = 0;
    screenImage.hidden = true;
    placeholder.hidden = false;
    timeline.innerHTML = '';
    playControls.hidden = true;
    thoughtText.textContent = '--';
    actionBadge.textContent = '--';
    actionDetail.textContent = '--';
    verifyText.textContent = '--';
    stepCount.textContent = '0 steps';
    metaSteps.textContent = '--';
    if (autoPlayTimer) clearInterval(autoPlayTimer);
    autoPlaying = false;
    $('#autoPlayBtn').textContent = '▶ 自动播放';
  }

  // === Keyboard shortcuts ===
  document.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight' && totalSteps > 0 && currentStep < totalSteps - 1) {
      loadStep(currentStep + 1);
    } else if (e.key === 'ArrowLeft' && currentStep > 0) {
      loadStep(currentStep - 1);
    } else if (e.key === ' ') {
      e.preventDefault();
      $('#autoPlayBtn').click();
    }
  });
})();
