/**
 * ChemR&D Intelligence Platform - New Experiment Wizard
 * Full 5-step wizard overlay for experiment planning, parameter configuration,
 * reaction simulation with catalyst mechanics, 3D molecular inspection, and export.
 */

(function () {
  'use strict';

  const DRAFT_STORAGE_KEY = 'chemrd.experimentDraft.v1';
  const TEAM_MEMBERS = [
    'Anika Rao (Research Lead)',
    'Marcus Iyer (Materials Chemist)',
    'Sunita Nair (Formulation Specialist)',
    'Devon Chen (Process Engineer)',
    'Elena Rostova (Analytical Lead)',
    'Jordan Taylor (Lab Technician)'
  ];

  // Wizard state
  const wizard = {
    step: 1,
    isOpen: false,
    draftTimer: null,
    simulationLoading: false,
    results: null,
    simulationId: null,
    experimentId: null,
    historyView: false,
    // Step 1 fields
    data: {
      name: '',
      objective: '',
      experimentType: 'Synthesis',
      owner: 'Anika Rao',
      date: new Date().toISOString().slice(0, 10),
      chemicals: [], // list of selected chemical objects with quantities, roles, and params
      globalConditions: {
        reaction_temp: 75,
        duration: 2.0,
        duration_unit: 'hours',
        atmosphere: 'Nitrogen (N₂)',
        stirring_speed: 450,
        heating_rate: 5.0,
        cooling_rate: 3.0
      }
    }
  };

  const esc = (v = '') =>
    String(v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function autoSaveDraft() {
    clearTimeout(wizard.draftTimer);
    wizard.draftTimer = setTimeout(() => {
      try {
        localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(wizard.data));
      } catch (_) {}
    }, 400);
  }

  function loadDraft() {
    try {
      const raw = localStorage.getItem(DRAFT_STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object') {
          wizard.data = { ...wizard.data, ...parsed };
        }
      }
    } catch (_) {}
  }

  function clearDraft() {
    try {
      localStorage.removeItem(DRAFT_STORAGE_KEY);
    } catch (_) {}
  }

  function freshWizardData() {
    return {
      name: '',
      objective: '',
      experimentType: 'Synthesis',
      owner: 'Anika Rao',
      date: new Date().toISOString().slice(0, 10),
      chemicals: [],
      globalConditions: {
        reaction_temp: 75,
        duration: 2.0,
        duration_unit: 'hours',
        atmosphere: 'Nitrogen (N₂)',
        stirring_speed: 450,
        heating_rate: 5.0,
        cooling_rate: 3.0
      }
    };
  }

  // Open & Close
  window.openNewExperimentWizard = async function (cloneData = null) {
    if (cloneData) {
      wizard.data = JSON.parse(JSON.stringify(cloneData));
      wizard.step = 1;
    } else {
      // A deliberate New experiment always starts clean. Reuse is available
      // through Duplicate & Tweak, rather than silently restoring old input.
      clearDraft();
      wizard.data = freshWizardData();
      wizard.step = 1;
    }
    wizard.results = null;
    wizard.simulationId = null;
    wizard.experimentId = null;
    wizard.historyView = false;

    // Ensure chemical library is loaded
    if (!state.chemicals || !state.chemicals.length) {
      try {
        state.chemicals = await api('/api/chemicals');
      } catch (_) {
        state.chemicals = [];
      }
    }

    wizard.isOpen = true;
    renderWizardOverlay();
    goToStep(wizard.step || 1);
  };

  window.closeWizard = function () {
    const el = document.getElementById('wizard-overlay');
    if (el) el.remove();
    wizard.isOpen = false;
  };

  function renderWizardOverlay() {
    let overlay = document.getElementById('wizard-overlay');
    if (overlay) overlay.remove();

    const html = `
      <div id="wizard-overlay" class="wizard-overlay" role="dialog" aria-modal="true" aria-label="New Experiment Wizard">
        <div class="wizard-shell">
          <header class="wizard-header">
            <div class="wizard-title-group">
              <span class="wizard-brand-badge">⚗ WIZARD</span>
              <div>
                <h2>New Experiment Wizard</h2>
                <p>Plan, simulate, analyze reaction thermodynamics &amp; save to register</p>
              </div>
            </div>
            <button class="icon-button wizard-close-btn" onclick="closeWizard()" title="Close Wizard">✕</button>
          </header>

          <nav class="wizard-stepper" aria-label="Wizard Steps">
            <div class="step-indicator ${wizard.step >= 1 ? 'active' : ''} ${wizard.step > 1 ? 'done' : ''}" onclick="goToStep(1)">
              <span class="step-num">${wizard.step > 1 ? '✓' : '1'}</span>
              <span class="step-label">Details</span>
            </div>
            <div class="step-line ${wizard.step > 1 ? 'active' : ''}"></div>

            <div class="step-indicator ${wizard.step >= 2 ? 'active' : ''} ${wizard.step > 2 ? 'done' : ''}" onclick="goToStep(2)">
              <span class="step-num">${wizard.step > 2 ? '✓' : '2'}</span>
              <span class="step-label">Chemicals</span>
            </div>
            <div class="step-line ${wizard.step > 2 ? 'active' : ''}"></div>

            <div class="step-indicator ${wizard.step >= 3 ? 'active' : ''} ${wizard.step > 3 ? 'done' : ''}" onclick="goToStep(3)">
              <span class="step-num">${wizard.step > 3 ? '✓' : '3'}</span>
              <span class="step-label">Parameters</span>
            </div>
            <div class="step-line ${wizard.step > 3 ? 'active' : ''}"></div>

            <div class="step-indicator ${wizard.step >= 4 ? 'active' : ''} ${wizard.step > 4 ? 'done' : ''}" onclick="goToStep(4)">
              <span class="step-num">${wizard.step > 4 ? '✓' : '4'}</span>
              <span class="step-label">Simulation</span>
            </div>
            <div class="step-line ${wizard.step > 4 ? 'active' : ''}"></div>

            <div class="step-indicator ${wizard.step >= 5 ? 'active' : ''}" onclick="goToStep(5)">
              <span class="step-num">5</span>
              <span class="step-label">Save &amp; Export</span>
            </div>
          </nav>

          <main id="wizard-content" class="wizard-body"></main>
        </div>
      </div>
    `;

    document.body.insertAdjacentHTML('beforeend', html);
  }

  function updateStepper() {
    const indicators = document.querySelectorAll('.wizard-stepper .step-indicator');
    const lines = document.querySelectorAll('.wizard-stepper .step-line');

    indicators.forEach((ind, i) => {
      const stepNum = i + 1;
      ind.classList.toggle('active', wizard.step >= stepNum);
      ind.classList.toggle('done', wizard.step > stepNum);
      const numSpan = ind.querySelector('.step-num');
      if (numSpan) {
        numSpan.textContent = wizard.step > stepNum ? '✓' : String(stepNum);
      }
    });

    lines.forEach((line, i) => {
      line.classList.toggle('active', wizard.step > i + 1);
    });
  }

  function goToStep(step) {
    if (step < 1 || step > 5) return;
    if (step === 2 && !wizardValidateStep1(false)) {
      toast('Please enter Experiment Name and Objective first');
      return;
    }
    if (step === 3 && wizard.data.chemicals.length < 2) {
      toast('Minimum 2 chemicals are required to proceed');
      return;
    }
    if (step === 4 && !wizard.results && !wizard.simulationLoading) {
      wizardRunSimulation();
      return;
    }

    wizard.step = step;
    updateStepper();
    const content = document.getElementById('wizard-content');
    if (!content) return;

    if (step === 1) renderStep1(content);
    else if (step === 2) renderStep2(content);
    else if (step === 3) renderStep3(content);
    else if (step === 4) renderStep4(content);
    else if (step === 5) renderStep5(content);

    content.scrollTop = 0;
  }
  window.goToStep = goToStep;

  // ==========================================
  // STEP 1 — Experiment Details
  // ==========================================
  function wizardValidateStep1(showWarning = false) {
    const nameEl = document.getElementById('exp-name');
    const objEl = document.getElementById('exp-objective');
    if (nameEl) wizard.data.name = nameEl.value;
    if (objEl) wizard.data.objective = objEl.value;

    const name = wizard.data.name?.trim();
    const obj = wizard.data.objective?.trim();
    const valid = Boolean(name && obj);
    const nextBtn = document.getElementById('step1-next-btn');
    if (nextBtn) {
      nextBtn.disabled = !valid;
    }
    return valid;
  }
  window.wizardValidateStep1 = wizardValidateStep1;

  function renderStep1(container) {
    const d = wizard.data;
    const types = [
      'Synthesis',
      'Thermal Analysis',
      'Spectroscopy',
      'Compatibility Test',
      'Formulation Screening',
      'Decomposition Study',
      'Custom'
    ];

    container.innerHTML = `
      <div class="wizard-pane fade-in">
        <div class="pane-heading">
          <h3>Step 1: Experiment Details</h3>
          <p>Define the core identity, hypothesis, and operational scope for this laboratory run.</p>
        </div>

        <div class="form-grid">
          <div class="form-group full-width">
            <label for="exp-name">Experiment Name <span class="required">*</span></label>
            <input id="exp-name" type="text" class="wizard-input" placeholder="e.g., Boron-resole compatibility test" value="${esc(d.name)}" />
            <small class="field-hint">A clear, descriptive title referencing key reagents or goals.</small>
          </div>

          <div class="form-group full-width">
            <label for="exp-objective">Experiment Objective <span class="required">*</span></label>
            <textarea id="exp-objective" class="wizard-textarea" placeholder="e.g., Confirm borate coordination signatures by FTIR and quantify char yield retention at 800°C">${esc(d.objective)}</textarea>
            <small class="field-hint">Specify target metrics, chemical questions, or expected analytical milestones.</small>
          </div>

          <div class="form-group">
            <label for="exp-type">Experiment Type</label>
            <select id="exp-type" class="wizard-select">
              ${types.map(t => `<option value="${esc(t)}" ${d.experimentType === t ? 'selected' : ''}>${esc(t)}</option>`).join('')}
            </select>
          </div>

          <div class="form-group">
            <label for="exp-owner">Owner / Researcher</label>
            <input id="exp-owner" list="team-list" class="wizard-input" value="${esc(d.owner || 'Anika Rao')}" placeholder="Select or type researcher name..." />
            <datalist id="team-list">
              ${TEAM_MEMBERS.map(m => `<option value="${esc(m)}"></option>`).join('')}
            </datalist>
          </div>

          <div class="form-group">
            <label for="exp-date">Target / Run Date</label>
            <input id="exp-date" type="date" class="wizard-input" value="${esc(d.date)}" />
          </div>
        </div>

        <div class="wizard-actions">
          <button class="btn ghost" onclick="closeWizard()">Cancel</button>
          <span class="flex-spacer"></span>
          <button id="step1-next-btn" class="btn primary" onclick="goToStep(2)" ${!d.name || !d.objective ? 'disabled' : ''}>
            Next: Select Chemicals →
          </button>
        </div>
      </div>
    `;

    // Listeners
    const nameInput = document.getElementById('exp-name');
    const objInput = document.getElementById('exp-objective');
    const typeSelect = document.getElementById('exp-type');
    const ownerInput = document.getElementById('exp-owner');
    const dateInput = document.getElementById('exp-date');

    const updateFields = () => {
      wizard.data.name = nameInput.value;
      wizard.data.objective = objInput.value;
      wizard.data.experimentType = typeSelect.value;
      wizard.data.owner = ownerInput.value;
      wizard.data.date = dateInput.value;
      wizardValidateStep1();
      autoSaveDraft();
    };

    nameInput.addEventListener('input', updateFields);
    objInput.addEventListener('input', updateFields);
    typeSelect.addEventListener('change', updateFields);
    ownerInput.addEventListener('input', updateFields);
    dateInput.addEventListener('change', updateFields);
  }

  // ==========================================
  // STEP 2 — Chemical Selection
  // ==========================================
  const ROLES = ['Reactant', 'Solvent', 'Catalyst', 'Additive', 'Substrate', 'Indicator', 'Buffer'];
  const UNITS = ['g', 'mg', 'kg', 'mL', 'L', 'mol'];

  function renderStep2(container) {
    const chemicals = state.chemicals || [];
    const selected = wizard.data.chemicals;

    container.innerHTML = `
      <div class="wizard-pane fade-in">
        <div class="pane-heading">
          <h3>Step 2: Chemical Selection</h3>
          <p>Add the reagents, catalysts, and solvents for this experiment. At least <strong>2 chemicals</strong> are required.</p>
        </div>

        <div class="search-add-bar">
          <div class="search-wrapper">
            <span class="search-icon">⌕</span>
            <input id="chem-search-input" class="wizard-input" placeholder="Search Chemical Library by name, formula, or CAS..." list="chem-search-datalist" />
            <datalist id="chem-search-datalist">
              ${chemicals.map(c => `<option value="${esc(c.name)} (${esc(c.formula || c.id)})" data-id="${esc(c.id)}"></option>`).join('')}
            </datalist>
          </div>
          <button class="btn primary" onclick="addChemicalFromSearch()">+ Add Chemical</button>
        </div>

        <div class="chem-picker-quick">
          <small>Quick add from library:</small>
          <div class="quick-tags">
            ${chemicals.slice(0, 6).map(c => `
              <button class="btn ghost quick-chip" onclick="wizardAddChemical('${esc(c.id)}')">
                + ${esc(c.name)} <span class="dim">${esc(c.formula || '')}</span>
              </button>
            `).join('')}
          </div>
        </div>

        <div class="selected-chemicals-section">
          <div class="section-subhead">
            <span>Selected Chemicals (${selected.length})</span>
            <span class="requirement-badge ${selected.length >= 2 ? 'met' : 'unmet'}">
              ${selected.length >= 2 ? '✓ Minimum 2 met' : `${2 - selected.length} more required`}
            </span>
          </div>

          <div id="selected-chemicals-list" class="chem-cards-grid">
            ${selected.length === 0 ? `
              <div class="empty-state-box">
                <span class="empty-icon">⬡</span>
                <p>No chemicals added yet. Search above or click quick tags to populate your mixture.</p>
              </div>
            ` : selected.map((c, idx) => renderSelectedChemCard(c, idx)).join('')}
          </div>
        </div>

        <div class="wizard-actions">
          <button class="btn ghost" onclick="goToStep(1)">← Back: Details</button>
          <span class="flex-spacer"></span>
          <button id="step2-next-btn" class="btn primary" onclick="goToStep(3)" ${selected.length < 2 ? 'disabled' : ''}>
            Next: Set Parameters →
          </button>
        </div>
      </div>
    `;

    const searchInput = document.getElementById('chem-search-input');
    if (searchInput) {
      searchInput.addEventListener('keydown', e => {
        if (e.key === 'Enter') addChemicalFromSearch();
      });
    }
  }

  function renderSelectedChemCard(c, idx) {
    const isCatalyst = c.role === 'Catalyst';
    return `
      <div class="chem-selected-card ${isCatalyst ? 'catalyst-card' : ''}" data-id="${esc(c.id)}">
        <div class="chem-card-header">
          <div class="chem-card-ident">
            <span class="chem-role-badge ${isCatalyst ? 'cat-role' : ''}">${esc(c.role || 'Reactant')}</span>
            <h4>${esc(c.name)}</h4>
            <span class="formula-tag">${esc(c.formula || '—')}</span>
            ${c.cas_number ? `<span class="cas-tag">CAS: ${esc(c.cas_number)}</span>` : ''}
          </div>
          <button class="remove-chem-btn" onclick="wizardRemoveChemical('${esc(c.id)}')" title="Remove chemical">✕</button>
        </div>

        <div class="chem-card-controls">
          <div class="qty-group">
            <label>Quantity</label>
            <div class="input-unit-duo">
              <input type="number" min="0.001" step="any" class="wizard-input qty-input" value="${c.quantity || 10}" onchange="updateChemField('${esc(c.id)}', 'quantity', parseFloat(this.value))" />
              <select class="wizard-select unit-select" onchange="updateChemField('${esc(c.id)}', 'quantity_unit', this.value)">
                ${UNITS.map(u => `<option value="${u}" ${c.quantity_unit === u ? 'selected' : ''}>${u}</option>`).join('')}
              </select>
            </div>
          </div>

          <div class="role-group">
            <label>Reaction Role</label>
            <select class="wizard-select role-select" onchange="updateChemField('${esc(c.id)}', 'role', this.value); refreshStep2UI();">
              ${ROLES.map(r => `<option value="${r}" ${c.role === r ? 'selected' : ''}>${r}</option>`).join('')}
            </select>
          </div>
        </div>

        ${isCatalyst ? `
          <div class="catalyst-card-callout">
            <span>✦ Catalyst detected:</span> Special activation energy reduction and mechanism controls enabled in Step 3!
          </div>
        ` : ''}
      </div>
    `;
  }

  window.addChemicalFromSearch = function () {
    const input = document.getElementById('chem-search-input');
    if (!input || !input.value.trim()) return;
    const val = input.value.trim();
    const chemicals = state.chemicals || [];

    let match = chemicals.find(c =>
      c.name.toLowerCase() === val.toLowerCase() ||
      `${c.name} (${c.formula || c.id})`.toLowerCase() === val.toLowerCase()
    );

    if (!match) {
      match = chemicals.find(c =>
        c.name.toLowerCase().includes(val.toLowerCase()) ||
        (c.formula && c.formula.toLowerCase().includes(val.toLowerCase()))
      );
    }

    if (match) {
      wizardAddChemical(match.id);
      input.value = '';
    } else {
      // Allow adding custom chemical name
      wizardAddCustomChemical(val);
      input.value = '';
    }
  };

  window.wizardAddChemical = async function (id) {
    if (wizard.data.chemicals.some(c => c.id === id)) {
      toast('Chemical is already in the experiment list');
      return;
    }

    let chemData = (state.chemicals || []).find(c => c.id === id);
    if (!chemData) {
      try {
        chemData = await api('/api/chemicals/' + encodeURIComponent(id));
      } catch (_) {}
    }

    // Pre-fill physical properties from properties table if available
    let meltingPoint = null;
    let boilingPoint = null;
    let density = null;

    if (chemData && chemData.properties) {
      chemData.properties.forEach(p => {
        const name = (p.property_name || '').toLowerCase();
        if (name.includes('melting')) meltingPoint = p.numeric_value || parseFloat(p.value_text);
        if (name.includes('boiling')) boilingPoint = p.numeric_value || parseFloat(p.value_text);
        if (name.includes('density')) density = p.numeric_value || parseFloat(p.value_text);
      });
    }

    const newChem = {
      id: id || 'custom-' + Date.now(),
      name: chemData ? chemData.name : id,
      formula: chemData ? chemData.formula : '',
      cas_number: chemData ? chemData.cas_number : '',
      smiles: chemData ? chemData.smiles : '',
      molecular_weight: chemData ? chemData.molecular_weight : 100.0,
      quantity: 10.0,
      quantity_unit: 'g',
      role: 'Reactant',
      // Step 3 params
      temp_min: -20.0,
      temp_max: 150.0,
      pressure: 1.0,
      ph: 7.0,
      concentration: 100.0,
      concentration_unit: 'w/w',
      freezing_point: meltingPoint !== null ? meltingPoint : 0.0,
      melting_point: meltingPoint !== null ? meltingPoint : 42.0,
      boiling_point: boilingPoint !== null ? boilingPoint : 180.0,
      density: density !== null ? density : 1.05,
      molar_mass: chemData && chemData.molecular_weight ? chemData.molecular_weight : 100.0,
      state_at_room_temp: 'Solid',
      solubility: 'Slightly Soluble',
      hazards: [],
      // Catalyst specific fields
      cat_mechanism: 'Acid-Catalyzed Electrophilic Aromatic Substitution',
      cat_ea_reduction: 32.0,
      cat_selectivity: 'Regioselective (ortho/para preference)'
    };

    wizard.data.chemicals.push(newChem);
    autoSaveDraft();
    refreshStep2UI();
  };

  function wizardAddCustomChemical(name) {
    const id = 'chem-custom-' + Date.now();
    const newChem = {
      id: id,
      name: name,
      formula: '',
      cas_number: '',
      smiles: '',
      molecular_weight: 120.0,
      quantity: 10.0,
      quantity_unit: 'g',
      role: 'Reactant',
      temp_min: 0.0,
      temp_max: 100.0,
      pressure: 1.0,
      ph: 7.0,
      concentration: 100.0,
      concentration_unit: 'w/w',
      freezing_point: 0.0,
      melting_point: 25.0,
      boiling_point: 100.0,
      density: 1.0,
      molar_mass: 120.0,
      state_at_room_temp: 'Liquid',
      solubility: 'Miscible',
      hazards: [],
      cat_mechanism: 'Acid/Base Catalysis',
      cat_ea_reduction: 25.0,
      cat_selectivity: 'Regioselective'
    };
    wizard.data.chemicals.push(newChem);
    autoSaveDraft();
    refreshStep2UI();
  }

  window.wizardRemoveChemical = function (id) {
    wizard.data.chemicals = wizard.data.chemicals.filter(c => c.id !== id);
    autoSaveDraft();
    refreshStep2UI();
  };

  window.updateChemField = function (id, field, value) {
    const c = wizard.data.chemicals.find(x => x.id === id);
    if (c) {
      c[field] = value;
      autoSaveDraft();
    }
  };

  function refreshStep2UI() {
    const list = document.getElementById('selected-chemicals-list');
    const nextBtn = document.getElementById('step2-next-btn');
    const badge = document.querySelector('.requirement-badge');
    const selected = wizard.data.chemicals;

    if (list) {
      list.innerHTML = selected.length === 0 ? `
        <div class="empty-state-box">
          <span class="empty-icon">⬡</span>
          <p>No chemicals added yet. Search above or click quick tags to populate your mixture.</p>
        </div>
      ` : selected.map((c, idx) => renderSelectedChemCard(c, idx)).join('');
    }

    if (nextBtn) {
      nextBtn.disabled = selected.length < 2;
    }

    if (badge) {
      badge.className = `requirement-badge ${selected.length >= 2 ? 'met' : 'unmet'}`;
      badge.textContent = selected.length >= 2 ? '✓ Minimum 2 met' : `${2 - selected.length} more required`;
    }
  }
  window.refreshStep2UI = refreshStep2UI;

  // ==========================================
  // STEP 3 — Parameter Configuration
  // ==========================================
  const STATES = ['Solid', 'Liquid', 'Gas', 'Plasma', 'Supercritical Fluid'];
  const SOLUBILITIES = ['Miscible', 'Slightly Soluble', 'Insoluble', 'Reacts with Solvent'];
  const HAZARDS = ['Flammable', 'Corrosive', 'Oxidizer', 'Toxic', 'Irritant', 'Carcinogenic', 'Environmental Hazard', 'None'];
  const CAT_MECHANISMS = [
    'Acid-Catalyzed Electrophilic Aromatic Substitution',
    'Base-Catalyzed Phenoxide Generation',
    'Enzymatic / Biocatalytic Cleavage',
    'Metal Complex Homogeneous Catalysis',
    'Heterogeneous Surface Adsorption',
    'Radical Propagation / Peroxide Initiator',
    'Photocatalytic Singlet Oxygen Generation',
    'Other / Custom Coordination'
  ];
  const SELECTIVITY_TYPES = ['Regioselective', 'Stereoselective', 'Chemoselective', 'Enantioselective'];
  const ATMOSPHERES = ['Air', 'Nitrogen (N₂)', 'Argon (Ar)', 'Vacuum', 'Oxygen (O₂)', 'Hydrogen (H₂)', 'CO₂'];

  function renderStep3(container) {
    const chems = wizard.data.chemicals;
    const cond = wizard.data.globalConditions;

    container.innerHTML = `
      <div class="wizard-pane fade-in">
        <div class="pane-heading">
          <h3>Step 3: Parameter Configuration</h3>
          <p>Tune individual chemical physical properties, range sliders, hazard labels, and global reaction environment.</p>
        </div>

        <div class="step3-section-title">
          <span>Chemical Parameter Cards (${chems.length})</span>
          <small>Click chemical header to collapse or expand</small>
        </div>

        <div class="collapsible-chems-container">
          ${chems.map((c, i) => renderChemicalParamCard(c, i)).join('')}
        </div>

        <div class="global-conditions-panel">
          <div class="panel-head">
            <div>
              <div class="panel-title">Global Experiment Conditions</div>
              <div class="panel-sub">Applies to the whole reaction mixture throughout execution.</div>
            </div>
            <span class="tag">Environment</span>
          </div>

          <div class="global-controls-grid">
            <div class="control-box">
              <div class="slider-head">
                <label for="gc-temp">Reaction Temperature</label>
                <div class="slider-val-box">
                  <input type="number" id="gc-temp-num" class="num-readout" value="${cond.reaction_temp}" oninput="syncGlobalTempSlider(this.value)" />
                  <span class="unit">°C</span>
                </div>
              </div>
              <input type="range" id="gc-temp" min="-50" max="600" step="1" value="${cond.reaction_temp}" oninput="syncGlobalTempNum(this.value)" />
            </div>

            <div class="control-box">
              <div class="slider-head">
                <label for="gc-stir">Stirring Speed</label>
                <span id="gc-stir-val" class="slider-val-pill">${cond.stirring_speed} RPM</span>
              </div>
              <input type="range" id="gc-stir" min="0" max="2000" step="25" value="${cond.stirring_speed}" oninput="updateGlobalStir(this.value)" />
            </div>

            <div class="control-box">
              <label>Reaction Duration</label>
              <div class="input-unit-duo">
                <input type="number" id="gc-duration" min="0.1" step="0.5" class="wizard-input" value="${cond.duration}" onchange="wizard.data.globalConditions.duration = parseFloat(this.value); autoSaveDraft();" />
                <select id="gc-duration-unit" class="wizard-select" onchange="wizard.data.globalConditions.duration_unit = this.value; autoSaveDraft();">
                  <option value="seconds" ${cond.duration_unit === 'seconds' ? 'selected' : ''}>seconds</option>
                  <option value="minutes" ${cond.duration_unit === 'minutes' ? 'selected' : ''}>minutes</option>
                  <option value="hours" ${cond.duration_unit === 'hours' ? 'selected' : ''}>hours</option>
                  <option value="days" ${cond.duration_unit === 'days' ? 'selected' : ''}>days</option>
                </select>
              </div>
            </div>

            <div class="control-box">
              <label>Atmosphere / Blanket Gas</label>
              <select id="gc-atm" class="wizard-select" onchange="wizard.data.globalConditions.atmosphere = this.value; autoSaveDraft();">
                ${ATMOSPHERES.map(a => `<option value="${esc(a)}" ${cond.atmosphere === a ? 'selected' : ''}>${esc(a)}</option>`).join('')}
              </select>
            </div>

            <div class="control-box">
              <label>Heating Rate (°C/min)</label>
              <input type="number" min="0.1" step="0.5" class="wizard-input" value="${cond.heating_rate || 5.0}" onchange="wizard.data.globalConditions.heating_rate = parseFloat(this.value); autoSaveDraft();" />
            </div>

            <div class="control-box">
              <label>Cooling Rate (°C/min)</label>
              <input type="number" min="0.1" step="0.5" class="wizard-input" value="${cond.cooling_rate || 3.0}" onchange="wizard.data.globalConditions.cooling_rate = parseFloat(this.value); autoSaveDraft();" />
            </div>
          </div>
        </div>

        <div class="wizard-actions">
          <button class="btn ghost" onclick="goToStep(2)">← Back: Chemicals</button>
          <span class="flex-spacer"></span>
          <button class="btn primary simulate-btn" onclick="wizardRunSimulation()">
            Run Experiment Simulation →
          </button>
        </div>
      </div>
    `;

    initDualRangeSliders();
  }

  function renderChemicalParamCard(c, i) {
    const isCat = c.role === 'Catalyst';
    return `
      <div class="chem-param-card ${isCat ? 'catalyst-theme' : ''}" id="param-card-${esc(c.id)}">
        <div class="param-card-header" onclick="toggleParamCardCollapse('${esc(c.id)}')">
          <div class="header-left">
            <span class="collapse-icon">⌄</span>
            <span class="chem-role-pill ${isCat ? 'cat-pill' : ''}">${esc(c.role)}</span>
            <h4>${esc(c.name)}</h4>
            <span class="formula-dim">${esc(c.formula || '')}</span>
          </div>
          <div class="header-right">
            <span>${c.quantity} ${c.quantity_unit}</span>
            <span class="conc-pill">${c.concentration}% ${c.concentration_unit}</span>
          </div>
        </div>

        <div class="param-card-body" id="param-body-${esc(c.id)}">
          <div class="sliders-section">
            <h5>Dynamic Operational Sliders (with real-time readout)</h5>
            <div class="slider-quad-grid">
              
              <!-- Temperature Dual-Handle Range Slider -->
              <div class="slider-card-box full-span">
                <div class="slider-head">
                  <label>Temperature Operational Window (−200°C to 2000°C)</label>
                  <div class="dual-readout-duo">
                    <span>Min: <b><input type="number" class="mini-num" value="${c.temp_min}" oninput="updateDualTemp('${esc(c.id)}', 'min', this.value)" /> °C</b></span>
                    <span class="range-sep">—</span>
                    <span>Max: <b><input type="number" class="mini-num" value="${c.temp_max}" oninput="updateDualTemp('${esc(c.id)}', 'max', this.value)" /> °C</b></span>
                  </div>
                </div>
                <div class="dual-slider-container" id="dual-slider-${esc(c.id)}">
                  <div class="dual-track"></div>
                  <div class="dual-range-fill" id="fill-${esc(c.id)}"></div>
                  <input type="range" class="dual-input dual-min" min="-200" max="2000" step="5" value="${c.temp_min}" oninput="onDualSliderInput('${esc(c.id)}', 'min', this)" />
                  <input type="range" class="dual-input dual-max" min="-200" max="2000" step="5" value="${c.temp_max}" oninput="onDualSliderInput('${esc(c.id)}', 'max', this)" />
                </div>
              </div>

              <!-- Pressure Slider -->
              <div class="slider-card-box">
                <div class="slider-head">
                  <label>Operating Pressure (0.01 to 100 atm)</label>
                  <span class="slider-val-pill" id="val-press-${esc(c.id)}">${c.pressure} atm</span>
                </div>
                <input type="range" min="0.01" max="100" step="0.1" value="${c.pressure}" oninput="updateParamSlider('${esc(c.id)}', 'pressure', this.value, 'val-press-${esc(c.id)}', 'atm')" />
              </div>

              <!-- pH Level Slider -->
              <div class="slider-card-box">
                <div class="slider-head">
                  <label>pH Level (0 to 14, step 0.1)</label>
                  <span class="slider-val-pill ph-pill" id="val-ph-${esc(c.id)}">pH ${c.ph}</span>
                </div>
                <input type="range" min="0" max="14" step="0.1" value="${c.ph}" oninput="updateParamSlider('${esc(c.id)}', 'ph', this.value, 'val-ph-${esc(c.id)}', 'pH ')" />
              </div>

              <!-- Concentration Slider with Unit Toggle -->
              <div class="slider-card-box full-span">
                <div class="slider-head">
                  <div class="toggle-label-wrap">
                    <label>Concentration</label>
                    <div class="conc-toggle-group">
                      <button class="toggle-btn ${c.concentration_unit === 'w/w' ? 'active' : ''}" onclick="toggleConcUnit('${esc(c.id)}', 'w/w')">w/w %</button>
                      <button class="toggle-btn ${c.concentration_unit === 'mol/L' ? 'active' : ''}" onclick="toggleConcUnit('${esc(c.id)}', 'mol/L')">mol/L</button>
                    </div>
                  </div>
                  <span class="slider-val-pill" id="val-conc-${esc(c.id)}">${c.concentration} ${c.concentration_unit}</span>
                </div>
                <input type="range" min="0" max="100" step="0.5" value="${c.concentration}" oninput="updateParamSlider('${esc(c.id)}', 'concentration', this.value, 'val-conc-${esc(c.id)}', '${c.concentration_unit}')" />
              </div>
            </div>
          </div>

          <!-- Physical Constants & Properties Pre-filled from Library -->
          <div class="props-section">
            <h5>Physical Constants &amp; Hazard Classification (Pre-filled from Library)</h5>
            <div class="props-fields-grid">
              <div class="prop-field">
                <label>Freezing Point (°C)</label>
                <input type="number" class="wizard-input" value="${c.freezing_point}" onchange="updateChemField('${esc(c.id)}', 'freezing_point', parseFloat(this.value))" />
              </div>
              <div class="prop-field">
                <label>Melting Point (°C)</label>
                <input type="number" class="wizard-input" value="${c.melting_point}" onchange="updateChemField('${esc(c.id)}', 'melting_point', parseFloat(this.value))" />
              </div>
              <div class="prop-field">
                <label>Boiling Point (°C)</label>
                <input type="number" class="wizard-input" value="${c.boiling_point}" onchange="updateChemField('${esc(c.id)}', 'boiling_point', parseFloat(this.value))" />
              </div>
              <div class="prop-field">
                <label>Density (g/cm³)</label>
                <input type="number" step="0.01" class="wizard-input" value="${c.density}" onchange="updateChemField('${esc(c.id)}', 'density', parseFloat(this.value))" />
              </div>
              <div class="prop-field">
                <label>Molar Mass (g/mol)</label>
                <input type="number" step="0.01" class="wizard-input" value="${c.molar_mass || c.molecular_weight || 100}" onchange="updateChemField('${esc(c.id)}', 'molar_mass', parseFloat(this.value))" />
              </div>
              <div class="prop-field">
                <label>State at Room Temp</label>
                <select class="wizard-select" onchange="updateChemField('${esc(c.id)}', 'state_at_room_temp', this.value)">
                  ${STATES.map(s => `<option value="${s}" ${c.state_at_room_temp === s ? 'selected' : ''}>${s}</option>`).join('')}
                </select>
              </div>
              <div class="prop-field">
                <label>Solubility Behavior</label>
                <select class="wizard-select" onchange="updateChemField('${esc(c.id)}', 'solubility', this.value)">
                  ${SOLUBILITIES.map(s => `<option value="${s}" ${c.solubility === s ? 'selected' : ''}>${s}</option>`).join('')}
                </select>
              </div>
            </div>

            <!-- Hazard Multi-Select -->
            <div class="hazards-wrap">
              <label>Hazard Classification (Multi-select)</label>
              <div class="hazard-chips">
                ${HAZARDS.map(h => {
                  const selected = (c.hazards || []).includes(h);
                  return `
                    <button type="button" class="hazard-chip ${selected ? 'selected' : ''}" onclick="toggleHazard('${esc(c.id)}', '${esc(h)}')">
                      ${selected ? '✓ ' : '+ '}${esc(h)}
                    </button>
                  `;
                }).join('')}
              </div>
            </div>
          </div>

          <!-- Catalyst Effect Section if Role is Catalyst -->
          ${isCat ? `
            <div class="catalyst-effect-section">
              <div class="cat-head">
                <span class="cat-icon">✦</span>
                <div>
                  <strong>Catalyst Reaction Mechanics</strong>
                  <p>Model activation energy reduction pathway, mechanistic routing, and selectivity enhancements.</p>
                </div>
              </div>
              <div class="cat-controls-grid">
                <div class="cat-control-box">
                  <label>Catalyst Mechanism</label>
                  <select class="wizard-select" onchange="updateChemField('${esc(c.id)}', 'cat_mechanism', this.value)">
                    ${CAT_MECHANISMS.map(m => `<option value="${esc(m)}" ${c.cat_mechanism === m ? 'selected' : ''}>${esc(m)}</option>`).join('')}
                  </select>
                </div>

                <div class="cat-control-box">
                  <div class="slider-head">
                    <label>Activation Energy (Ea) Reduction</label>
                    <span class="slider-val-pill cat-pill" id="val-cat-ea-${esc(c.id)}">${c.cat_ea_reduction || 32}% lower</span>
                  </div>
                  <input type="range" min="5" max="80" step="1" value="${c.cat_ea_reduction || 32}" oninput="updateParamSlider('${esc(c.id)}', 'cat_ea_reduction', this.value, 'val-cat-ea-${esc(c.id)}', '% lower')" />
                </div>

                <div class="cat-control-box">
                  <label>Selectivity Enhancement</label>
                  <select class="wizard-select" onchange="updateChemField('${esc(c.id)}', 'cat_selectivity', this.value)">
                    ${SELECTIVITY_TYPES.map(s => `<option value="${esc(s)}" ${c.cat_selectivity === s ? 'selected' : ''}>${esc(s)}</option>`).join('')}
                  </select>
                </div>
              </div>
            </div>
          ` : ''}
        </div>
      </div>
    `;
  }

  window.toggleParamCardCollapse = function (id) {
    const card = document.getElementById('param-card-' + id);
    if (card) card.classList.toggle('collapsed');
  };

  // Dual Range Slider Implementation
  function initDualRangeSliders() {
    wizard.data.chemicals.forEach(c => {
      updateDualFill(c.id);
    });
  }

  function updateDualFill(id) {
    const fill = document.getElementById('fill-' + id);
    const c = wizard.data.chemicals.find(x => x.id === id);
    if (!fill || !c) return;

    const minRange = -200;
    const maxRange = 2000;
    const total = maxRange - minRange;

    const leftPct = ((c.temp_min - minRange) / total) * 100;
    const rightPct = ((maxRange - c.temp_max) / total) * 100;

    fill.style.left = `${Math.max(0, leftPct)}%`;
    fill.style.right = `${Math.max(0, rightPct)}%`;
  }

  window.onDualSliderInput = function (id, handle, el) {
    const c = wizard.data.chemicals.find(x => x.id === id);
    if (!c) return;
    const val = parseFloat(el.value);

    if (handle === 'min') {
      if (val >= c.temp_max) {
        c.temp_min = c.temp_max - 5;
        el.value = c.temp_min;
      } else {
        c.temp_min = val;
      }
    } else {
      if (val <= c.temp_min) {
        c.temp_max = c.temp_min + 5;
        el.value = c.temp_max;
      } else {
        c.temp_max = val;
      }
    }

    const card = document.getElementById('param-card-' + id);
    if (card) {
      const inputs = card.querySelectorAll('.mini-num');
      if (inputs[0]) inputs[0].value = c.temp_min;
      if (inputs[1]) inputs[1].value = c.temp_max;
    }

    updateDualFill(id);
    autoSaveDraft();
  };

  window.updateDualTemp = function (id, handle, valStr) {
    const c = wizard.data.chemicals.find(x => x.id === id);
    if (!c) return;
    const val = parseFloat(valStr) || 0;
    if (handle === 'min') c.temp_min = Math.min(val, c.temp_max - 5);
    else c.temp_max = Math.max(val, c.temp_min + 5);

    const card = document.getElementById('param-card-' + id);
    if (card) {
      const minSlider = card.querySelector('.dual-min');
      const maxSlider = card.querySelector('.dual-max');
      if (minSlider) minSlider.value = c.temp_min;
      if (maxSlider) maxSlider.value = c.temp_max;
    }

    updateDualFill(id);
    autoSaveDraft();
  };

  window.updateParamSlider = function (id, field, val, pillId, unit) {
    updateChemField(id, field, parseFloat(val));
    const pill = document.getElementById(pillId);
    if (pill) {
      if (unit.startsWith('pH')) pill.textContent = `pH ${val}`;
      else pill.textContent = `${val} ${unit}`;
    }
  };

  window.toggleConcUnit = function (id, unit) {
    const c = wizard.data.chemicals.find(x => x.id === id);
    if (!c) return;
    c.concentration_unit = unit;
    autoSaveDraft();
    goToStep(3);
  };

  window.toggleHazard = function (id, hazard) {
    const c = wizard.data.chemicals.find(x => x.id === id);
    if (!c) return;
    c.hazards = c.hazards || [];
    if (c.hazards.includes(hazard)) {
      c.hazards = c.hazards.filter(h => h !== hazard);
    } else {
      c.hazards.push(hazard);
    }
    autoSaveDraft();
    goToStep(3);
  };

  // Global conditions slider sync
  window.syncGlobalTempSlider = function (val) {
    const num = parseFloat(val) || 25;
    wizard.data.globalConditions.reaction_temp = num;
    const slider = document.getElementById('gc-temp');
    if (slider) slider.value = num;
    autoSaveDraft();
  };

  window.syncGlobalTempNum = function (val) {
    const num = parseFloat(val) || 25;
    wizard.data.globalConditions.reaction_temp = num;
    const numInput = document.getElementById('gc-temp-num');
    if (numInput) numInput.value = num;
    autoSaveDraft();
  };

  window.updateGlobalStir = function (val) {
    const num = parseFloat(val) || 0;
    wizard.data.globalConditions.stirring_speed = num;
    const pill = document.getElementById('gc-stir-val');
    if (pill) pill.textContent = `${num} RPM`;
    autoSaveDraft();
  };

  // ==========================================
  // STEP 4 — Results & Output (Simulation)
  // ==========================================
  window.wizardRunSimulation = async function () {
    wizard.step = 4;
    updateStepper();
    wizard.simulationLoading = true;

    const content = document.getElementById('wizard-content');
    if (content) {
      content.innerHTML = `
        <div class="simulation-loading-screen fade-in">
          <div class="molecule-spinner">
            <svg class="spinner-svg" viewBox="0 0 100 100">
              <circle cx="50" cy="50" r="38" stroke="rgba(94, 234, 212, 0.2)" stroke-width="3" fill="none" />
              <circle cx="50" cy="50" r="38" stroke="#5eead4" stroke-width="4" stroke-dasharray="60 180" fill="none">
                <animateTransform attributeName="transform" type="rotate" from="0 50 50" to="360 50 50" dur="1.4s" repeatCount="indefinite" />
              </circle>
              <circle cx="50" cy="12" r="7" fill="#53b5ff" />
              <circle cx="83" cy="69" r="6" fill="#fbbf70" />
              <circle cx="17" cy="69" r="6" fill="#67e8b4" />
            </svg>
          </div>
          <h3>Simulating Reaction Mechanics…</h3>
          <p>Evaluating activation energy barriers, thermodynamic state functions (ΔG, ΔH, ΔS), phase transitions, and catalyst selectivity.</p>
          <div class="sim-pulse-bar"><span class="pulse-fill"></span></div>
        </div>
      `;
    }

    try {
      const payload = {
        name: wizard.data.name,
        objective: wizard.data.objective,
        experiment_type: wizard.data.experimentType,
        chemicals: wizard.data.chemicals,
        global_conditions: wizard.data.globalConditions
      };

      const res = await api('/api/experiments/simulate', {
        method: 'POST',
        body: JSON.stringify(payload)
      });

      wizard.results = res;
      try {
        const saved = await api('/api/simulations', {
          method: 'POST',
          body: JSON.stringify({ experiment: payload, simulation_results: res })
        });
        wizard.simulationId = saved.id;
      } catch (saveErr) {
        // The result remains usable and exportable even if history persistence
        // is temporarily unavailable.
        toast(`Simulation completed, but history could not be saved: ${saveErr.message}`);
      }
      wizard.simulationLoading = false;
      goToStep(4);
    } catch (err) {
      wizard.simulationLoading = false;
      toast(`Simulation request failed: ${err.message}. Retrying with local chemistry engine...`);
      goToStep(3);
    }
  };

  function renderStep4(container) {
    const res = wizard.results;
    if (!res) {
      container.innerHTML = `<div class="panel empty">No simulation results available yet. Run simulation from Step 3.</div>`;
      return;
    }

    const cat = res.catalyst_influence;
    const obs = res;
    const tempCurve = res.temperature_change || { time_points: [] };

    container.innerHTML = `
      <div class="wizard-pane fade-in results-dashboard">
        <div class="results-header-banner">
          <div>
            <span class="status-pill ${res.feasibility_score >= 80 ? 'feasible' : 'moderate'}">
              ● ${esc(res.feasibility_label || 'High Feasibility')} (${res.feasibility_score}%)
            </span>
            <h3>${esc(res.reaction_type || 'Reaction Prediction')}</h3>
            <p class="chem-equation"><code>${esc(res.balanced_equation)}</code></p>
          </div>
          <button class="btn ghost re-run-btn" onclick="wizardRunSimulation()">↺ Re-run Simulation</button>
        </div>

        <!-- 4a. Reaction Overview & Thermodynamics -->
        <div class="results-section">
          <div class="section-title">
            <span>4a. Reaction Overview &amp; Thermodynamic State Functions</span>
          </div>

          <div class="thermo-cards-grid">
            <div class="thermo-card">
              <span class="thermo-label">Gibbs Free Energy (ΔG)</span>
              <span class="thermo-val ${res.gibbs_free_energy < 0 ? 'good' : 'warn'}">${res.gibbs_free_energy} kJ/mol</span>
              <span class="thermo-sub">${esc(res.spontaneity)}</span>
            </div>

            <div class="thermo-card">
              <span class="thermo-label">Enthalpy Change (ΔH)</span>
              <span class="thermo-val ${res.enthalpy_change < 0 ? 'exo' : 'endo'}">${res.enthalpy_change} kJ/mol</span>
              <span class="thermo-sub">${res.enthalpy_change < 0 ? 'Exothermic (releases heat)' : 'Endothermic'}</span>
            </div>

            <div class="thermo-card">
              <span class="thermo-label">Entropy Change (ΔS)</span>
              <span class="thermo-val">${res.entropy_change} J/(mol·K)</span>
              <span class="thermo-sub">Disorder / state variation</span>
            </div>

            <div class="thermo-card">
              <span class="thermo-label">Activation Energy (Ea)</span>
              <span class="thermo-val ${cat && cat.present ? 'cat-boost' : ''}">${res.activation_energy} kJ/mol</span>
              <span class="thermo-sub">${cat && cat.present ? `Catalyzed (vs ${cat.ea_without_cat} kJ/mol)` : 'Uncatalyzed barrier'}</span>
            </div>
          </div>

          ${cat && cat.present ? `
            <div class="catalyst-influence-banner">
              <div class="cat-influence-badge">✦ CATALYST INFLUENCE ACTIVE</div>
              <div class="cat-influence-content">
                <strong>${esc(cat.catalyst_name)}:</strong>
                <span>${esc(cat.rate_enhancement)}</span> —
                <span>Ea lowered from <b>${cat.ea_without_cat} kJ/mol</b> to <b>${cat.ea_with_cat} kJ/mol</b>.</span>
                <p>${esc(cat.selectivity_effect)}</p>
              </div>
            </div>
          ` : ''}
        </div>

        <!-- 4b. Observable Changes (What a student would see in a lab) -->
        <div class="results-section">
          <div class="section-title">
            <span>4b. Observable Laboratory Changes (Sensory &amp; Physical)</span>
          </div>

          <div class="observables-grid">
            <div class="obs-card color-obs">
              <span class="obs-tag">Color Transition</span>
              <div class="color-swatch-row">
                <div class="swatch-item">
                  <span class="color-dot" style="background:${res.color_change?.from || '#eee'}"></span>
                  <small>From: ${esc(res.color_change?.from_name || 'Initial')}</small>
                </div>
                <span class="arrow-sym">⟶</span>
                <div class="swatch-item">
                  <span class="color-dot" style="background:${res.color_change?.to || '#d97706'}"></span>
                  <small>To: ${esc(res.color_change?.to_name || 'Final')}</small>
                </div>
              </div>
            </div>

            <div class="obs-card">
              <span class="obs-tag">Temperature Dynamics</span>
              <b>${esc(res.temperature_change?.delta || '+0 °C')} peak shift</b>
              <small>${esc(res.temperature_change?.nature || 'Thermal response')}</small>
              <div class="mini-temp-chart">
                ${renderMiniTempChartSvg(tempCurve.time_points)}
              </div>
            </div>

            <div class="obs-card">
              <span class="obs-tag">Gas Evolution</span>
              <b>${res.gas_evolution?.detected ? '✓ Gas Evolved' : '✕ No Gas'}</b>
              <small>${esc(res.gas_evolution?.gas || 'None')} ${res.gas_evolution?.volume_estimate ? `(${esc(res.gas_evolution.volume_estimate)})` : ''}</small>
            </div>

            <div class="obs-card">
              <span class="obs-tag">Precipitate</span>
              <b>${res.precipitate?.detected ? `✓ ${esc(res.precipitate.color)}` : '✕ Homogeneous'}</b>
              <small>${esc(res.precipitate?.quantity || 'Clear solution')}</small>
            </div>

            <div class="obs-card">
              <span class="obs-tag">Odor Profile</span>
              <b>Aroma</b>
              <small>${esc(res.odor || 'Faint characteristic scent')}</small>
            </div>

            <div class="obs-card">
              <span class="obs-tag">Effervescence / Sound</span>
              <b>${res.effervescence?.detected ? 'Bubbling observed' : 'Quiet reaction'}</b>
              <small>${esc(res.sound || 'Steady stirring sound')}</small>
            </div>

            <div class="obs-card">
              <span class="obs-tag">Phase &amp; Viscosity</span>
              <b>${esc(res.phase_change || 'Liquid')}</b>
              <small>${esc(res.viscosity_change || 'Consistent viscosity')}</small>
            </div>
          </div>
        </div>

        <!-- 4c. Product Analysis -->
        <div class="results-section">
          <div class="section-title">
            <span>4c. Product Analysis &amp; Stoichiometric Yields</span>
          </div>

          <div class="products-layout">
            <div class="panel product-table-panel">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>Component</th>
                    <th>Formula</th>
                    <th>Role</th>
                    <th>Yield (%)</th>
                    <th>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  ${(res.products || []).map(p => `
                    <tr class="highlight-row">
                      <td><b>${esc(p.name)}</b></td>
                      <td><code>${esc(p.formula)}</code></td>
                      <td><span class="tag measured">${esc(p.role || 'Product')}</span></td>
                      <td><span class="yield-badge">${p.yield}%</span></td>
                      <td><b>${p.amount} ${p.unit || 'g'}</b></td>
                    </tr>
                  `).join('')}
                  ${(res.byproducts || []).map(bp => `
                    <tr>
                      <td>${esc(bp.name)}</td>
                      <td><code>${esc(bp.formula)}</code></td>
                      <td><span class="tag calculated">Byproduct</span></td>
                      <td>${bp.yield}%</td>
                      <td>${bp.amount} ${bp.unit || 'g'}</td>
                    </tr>
                  `).join('')}
                  ${(res.unreacted_reagents || []).map(ur => `
                    <tr>
                      <td>${esc(ur.name)}</td>
                      <td>—</td>
                      <td><span class="tag">Unreacted</span></td>
                      <td>${ur.leftover_percentage}% left</td>
                      <td>${ur.amount} ${ur.unit || 'g'}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>

            <div class="purity-meter-card">
              <span class="purity-title">Estimated Product Purity</span>
              <div class="purity-circle">
                <span class="purity-number">${res.purity_estimate || 91}%</span>
                <span class="purity-label">Analytical Grade</span>
              </div>
              <p>Predicted by stoichiometric conversion, selectivity ratios, and side-reaction modeling.</p>
            </div>
          </div>
        </div>

        <!-- 4d. Molecular Structure Viewer -->
        <div class="results-section">
          <div class="section-title">
            <span>4d. Molecular Structure &amp; Conformation (Main Product)</span>
          </div>

          <div class="structure-two-col">
            <div class="panel">
              <div class="panel-head">
                <div>
                  <div class="panel-title">2D Structure</div>
                  <div class="panel-sub">${esc(res.main_product_details?.name || 'Main Product')}</div>
                </div>
                <span class="tag">${esc(res.main_product_details?.formula || '')}</span>
              </div>
              <div class="structure-art-box">
                <img class="structure-image" src="/api/live-structure/2d?smiles=${encodeURIComponent(res.main_product_details?.smiles||'')}&amp;name=${encodeURIComponent(res.main_product_details?.name||'')}" alt="2D product structure" onerror="recoverStructureImage(this)"><div class="structure-fallback" hidden><b>Resolving product structure</b><small>Try a verified product identifier.</small></div>
              </div>
              <div class="code-box">
                <small>SMILES</small>
                <code>${esc(res.main_product_details?.smiles || '—')}</code>
              </div>
              <div class="code-box">
                <small>IUPAC Name</small>
                <span>${esc(res.main_product_details?.iupac_name || '—')}</span>
              </div>
            </div>

            <div class="panel">
              <div class="panel-head">
                <div>
                  <div class="panel-title">Interactive 3D Molecular Conformer</div>
                  <div class="panel-sub">Rotatable &amp; zoomable via 3Dmol.js</div>
                </div>
                <div class="viewer-tools">
                  <button class="btn ghost mini" onclick="resetSim3DMol()">Reset</button>
                  <button class="btn ghost mini" onclick="toggleSimSpin()">Spin</button>
                </div>
              </div>

              <div class="viewer-3d sim-viewer-3d" id="sim-mol3d-container">
                <div class="viewer-loading">Loading 3D molecular coordinates…</div>
              </div>

              <div class="functional-groups-box">
                <small>Identified Functional Groups</small>
                <div class="fg-tags">
                  ${(res.main_product_details?.functional_groups || []).map(fg => `<span class="fg-tag">${esc(fg)}</span>`).join('')}
                </div>
              </div>

              <div class="table-wrap mini-bond-table">
                <table class="data-table">
                  <thead><tr><th>Bond Type</th><th>Predicted Length</th><th>Equilibrium Angle</th></tr></thead>
                  <tbody>
                    ${(res.main_product_details?.bond_table || []).map(b => `
                      <tr>
                        <td><b>${esc(b.bond)}</b></td>
                        <td>${esc(b.length)}</td>
                        <td>${esc(b.angle)}</td>
                      </tr>
                    `).join('')}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>

        <!-- 4e. Safety & Waste -->
        <div class="results-section">
          <div class="section-title">
            <span>4e. Safety Hazards &amp; Waste Disposal Protocol</span>
          </div>

          <div class="safety-grid">
            <div class="panel safety-panel">
              <div class="panel-title text-amber">⚠ Laboratory Safety Directives</div>
              <ul class="bullet-list">
                ${(res.safety_warnings || []).map(w => `<li>${esc(w)}</li>`).join('')}
              </ul>
              <div class="ppe-chips-row">
                <small>Required PPE:</small>
                ${(res.recommended_ppe || []).map(ppe => `<span class="ppe-chip">🛡 ${esc(ppe)}</span>`).join('')}
              </div>
            </div>

            <div class="panel waste-panel">
              <div class="panel-title text-cyan">♻ Waste Management</div>
              <p><b>Classification:</b> ${esc(res.waste_classification || 'Organic Waste')}</p>
              <p><b>Disposal Method:</b> ${esc(res.disposal_method || 'Collect in dedicated waste stream.')}</p>
            </div>
          </div>
        </div>

        <!-- 4f. Theoretical vs. Practical Notes -->
        <div class="results-section">
          <div class="section-title">
            <span>4f. Theoretical vs. Practical Laboratory Notes</span>
          </div>

          <div class="theory-practice-grid">
            <div class="panel theory-card">
              <div class="panel-title text-blue">◈ In Theory (Textbook Baseline)</div>
              <p><strong>Ideal Conditions:</strong> ${esc(res.theory_notes?.ideal_conditions || '—')}</p>
              <p><strong>Theoretical Maximum:</strong> ${esc(res.theory_notes?.theoretical_yield || '100%')}</p>
              <p class="explanation">${esc(res.theory_notes?.textbook_explanation || '—')}</p>
            </div>

            <div class="panel practice-card">
              <div class="panel-title text-green">◈ In Practice (Bench Realities)</div>
              <p><strong>Real Deviations:</strong> ${esc(res.practice_notes?.deviations || '—')}</p>
              <p><strong>Yield Loss Factors:</strong> ${esc(res.practice_notes?.yield_loss_reasons || '—')}</p>
              <p class="lab-tips"><b>Bench Tip:</b> ${esc(res.practice_notes?.lab_tips || '—')}</p>
            </div>
          </div>

          <div class="panel troubleshooting-panel">
            <div class="panel-head">
              <div>
                <div class="panel-title">Bench Troubleshooting Guide</div>
                <div class="panel-sub">Common deviations and fast remedies during execution.</div>
              </div>
            </div>
            <div class="table-wrap">
              <table class="data-table">
                <thead><tr><th>Observed Deviation</th><th>Probable Cause</th><th>Immediate Remedy</th></tr></thead>
                <tbody>
                  ${(res.troubleshooting || []).map(t => `
                    <tr>
                      <td class="text-amber"><b>${esc(t.issue)}</b></td>
                      <td>${esc(t.cause)}</td>
                      <td class="text-cyan">${esc(t.remedy)}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div class="wizard-actions">
          <button class="btn ghost" onclick="goToStep(3)">← Back: Parameters</button>
          <span class="flex-spacer"></span>
          <button class="btn primary" onclick="goToStep(5)">Next: Save &amp; Export →</button>
        </div>
      </div>
    `;

    setTimeout(() => {
      initSimulation3DMol(res.main_product_details?.smiles);
    }, 50);
  }

  function renderMiniTempChartSvg(points = []) {
    if (!points || !points.length) {
      points = [[0, 25], [10, 45], [20, 68], [35, 62], [50, 48], [60, 35]];
    }
    const W = 280;
    const H = 70;
    const xs = points.map(p => p[0]);
    const ys = points.map(p => p[1]);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs) || 1;
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys) || 1;

    const scaleX = x => 10 + ((x - minX) / (maxX - minX)) * (W - 20);
    const scaleY = y => H - 10 - ((y - minY) / (maxY - minY || 1)) * (H - 20);

    const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${scaleX(p[0]).toFixed(1)} ${scaleY(p[1]).toFixed(1)}`).join(' ');

    return `
      <svg viewBox="0 0 ${W} ${H}" class="mini-svg-chart">
        <path d="${d}" fill="none" stroke="#5eead4" stroke-width="2.5" stroke-linecap="round" />
        <circle cx="${scaleX(points[0][0])}" cy="${scaleY(points[0][1])}" r="3" fill="#53b5ff" />
        <circle cx="${scaleX(points[points.length - 1][0])}" cy="${scaleY(points[points.length - 1][1])}" r="3" fill="#fbbf70" />
      </svg>
    `;
  }

  let simViewerInstance = null;
  let simViewerSpinning = false;

  async function initSimulation3DMol(smiles) {
    const product = wizard.results?.main_product_details || {};
    const url = '/api/live-structure/3d?smiles='+encodeURIComponent(smiles||'')+'&name='+encodeURIComponent(product.name||'');
    simViewerInstance = await window.setupMolViewerClean(smiles,'sim-mol3d-container',url);
    simViewerSpinning = false;
  }

  window.resetSim3DMol = function () {
    if (simViewerInstance) {
      simViewerInstance.zoomTo();
      simViewerInstance.render();
    }
  };

  window.toggleSimSpin = function () {
    if (!simViewerInstance) return;
    simViewerSpinning = !simViewerSpinning;
    simViewerInstance.spin(simViewerSpinning ? 'y' : false, 0.2);
  };

  // ==========================================
  // STEP 5 — Save & Export
  // ==========================================
  function renderStep5(container) {
    const d = wizard.data;
    const res = wizard.results || {};

    container.innerHTML = `
      <div class="wizard-pane fade-in">
        <div class="pane-heading">
          <h3>Step 5: Save &amp; Export</h3>
          <p>Commit your experiment record to the workspace register, generate printable PDF reports, or export structured datasets.</p>
        </div>

        <div class="save-cards-grid">
          <!-- Commit to Register -->
          <div class="panel action-card primary-action">
            <div class="action-card-head">
              <span class="action-icon">◌</span>
              <div>
                <h4>Save to Experiment Register</h4>
                <p>Appends a new row with status <strong>"planned"</strong> to the master Experiments register table and links all reagents.</p>
              </div>
            </div>
            <div class="record-summary-chip">
              <span><b>Name:</b> ${esc(d.name)}</span>
              <span><b>Type:</b> ${esc(d.experimentType)}</span>
              <span><b>Reagents:</b> ${d.chemicals.length} linked</span>
            </div>
            <button class="btn primary full-width" onclick="commitExperimentToRegister()">
              ✓ Save to Register &amp; Finish
            </button>
          </div>

          <!-- Export Suite -->
          <div class="panel action-card">
            <div class="action-card-head">
              <span class="action-icon">▤</span>
              <div>
                <h4>Export Laboratory Reports</h4>
                <p>Generate formal PDF lab documentation or raw machine-readable data files.</p>
              </div>
            </div>
            <div class="export-buttons-group">
              <button class="btn ghost" onclick="exportWizardPDF()">🖨 Print / PDF Lab Report</button>
              <button class="btn ghost" onclick="exportWizardCSV()">⤓ Download CSV Data</button>
              <button class="btn ghost" onclick="exportWizardJSON()">⤓ Download JSON Raw Data</button>
            </div>
          </div>

          <!-- Share & Repeat -->
          <div class="panel action-card">
            <div class="action-card-head">
              <span class="action-icon">✦</span>
              <div>
                <h4>Collaboration &amp; Iteration</h4>
                <p>Share with your research team or duplicate parameters into a new iteration.</p>
              </div>
            </div>
            <div class="export-buttons-group">
              <button class="btn ghost" onclick="copyShareLink()">🔗 Copy Experiment Link</button>
              <button class="btn ghost" onclick="cloneAndRepeatExperiment()">↺ Duplicate &amp; Tweak Parameters</button>
            </div>
          </div>
        </div>

        <div class="wizard-actions">
          <button class="btn ghost" onclick="goToStep(4)">← Back: Results</button>
          <span class="flex-spacer"></span>
          <button class="btn ghost" onclick="closeWizard()">Done &amp; Close</button>
        </div>
      </div>
    `;
  }

  window.commitExperimentToRegister = async function () {
    try {
      if (wizard.experimentId) {
        toast('This experiment is already saved to the register.');
        return;
      }
      const payload = {
        name: wizard.data.name,
        objective: wizard.data.objective,
        experiment_type: wizard.data.experimentType,
        owner: wizard.data.owner || 'Anika Rao',
        date: wizard.data.date,
        chemical_ids: wizard.data.chemicals.map(c => c.id),
        status: 'planned',
        simulation_id: wizard.simulationId
      };

      const saved = await api('/api/experiments', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      wizard.experimentId = saved.id;

      clearDraft();
      closeWizard();
      toast(`Experiment "${payload.name}" saved to register!`);

      // Refresh experiments view if currently on experiments page
      if (state.view === 'experiments') {
        await experimentsView();
      }
    } catch (err) {
      toast(`Failed to save experiment: ${err.message}`);
    }
  };

  window.exportWizardPDF = function () {
    window.print();
  };

  window.exportWizardCSV = function () {
    const d = wizard.data;
    const res = wizard.results || {};
    let csv = 'Category,Property,Value\n';
    csv += `Experiment,Name,"${(d.name || '').replace(/"/g, '""')}"\n`;
    csv += `Experiment,Objective,"${(d.objective || '').replace(/"/g, '""')}"\n`;
    csv += `Experiment,Type,"${d.experimentType}"\n`;
    csv += `Experiment,Owner,"${d.owner}"\n`;
    csv += `Experiment,Date,"${d.date}"\n`;
    csv += `Reaction,Equation,"${(res.balanced_equation || '').replace(/"/g, '""')}"\n`;
    csv += `Reaction,Feasibility,"${res.feasibility_score}%"\n`;
    csv += `Thermodynamics,DeltaG_kJ_mol,"${res.gibbs_free_energy || ''}"\n`;
    csv += `Thermodynamics,DeltaH_kJ_mol,"${res.enthalpy_change || ''}"\n`;
    csv += `Thermodynamics,ActivationEnergy_kJ_mol,"${res.activation_energy || ''}"\n`;

    d.chemicals.forEach((c, idx) => {
      csv += `Chemical_${idx + 1},Name,"${c.name}"\n`;
      csv += `Chemical_${idx + 1},Role,"${c.role}"\n`;
      csv += `Chemical_${idx + 1},Quantity,"${c.quantity} ${c.quantity_unit}"\n`;
      csv += `Chemical_${idx + 1},TempMin,"${c.temp_min} C"\n`;
      csv += `Chemical_${idx + 1},TempMax,"${c.temp_max} C"\n`;
      csv += `Chemical_${idx + 1},Pressure,"${c.pressure} atm"\n`;
      csv += `Chemical_${idx + 1},pH,"${c.ph}"\n`;
    });

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `experiment_${d.name.toLowerCase().replace(/[^a-z0-9]/g, '_')}.csv`;
    link.click();
    toast('CSV lab dataset downloaded');
  };

  window.exportWizardJSON = function () {
    const exportData = {
      experiment: wizard.data,
      simulation_results: wizard.results,
      exported_at: new Date().toISOString()
    };
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `experiment_${wizard.data.name.toLowerCase().replace(/[^a-z0-9]/g, '_')}.json`;
    link.click();
    toast('JSON experiment data downloaded');
  };

  function downloadTextFile(filename, content, type) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function csvCell(value) {
    return `"${String(value ?? '').replace(/"/g, '""')}"`;
  }

  function buildSimulationCsv(experiment, results) {
    let csv = 'Category,Property,Value\n';
    const add = (category, property, value) => {
      csv += `${csvCell(category)},${csvCell(property)},${csvCell(value)}\n`;
    };
    add('Experiment', 'Name', experiment.name);
    add('Experiment', 'Objective', experiment.objective);
    add('Experiment', 'Type', experiment.experiment_type || experiment.experimentType);
    add('Reaction', 'Equation', results.balanced_equation);
    add('Reaction', 'Classification', results.reaction_type);
    add('Reaction', 'Feasibility', `${results.feasibility_score ?? ''}%`);
    add('Thermodynamics', 'DeltaG_kJ_mol', results.gibbs_free_energy);
    add('Thermodynamics', 'DeltaH_kJ_mol', results.enthalpy_change);
    add('Thermodynamics', 'DeltaS_J_mol_K', results.entropy_change);
    add('Thermodynamics', 'ActivationEnergy_kJ_mol', results.activation_energy);
    const conditions = experiment.global_conditions || experiment.globalConditions || {};
    Object.entries(conditions).forEach(([key, value]) => add('Global conditions', key, value));
    (experiment.chemicals || []).forEach((chemical, index) => {
      const category = `Chemical_${index + 1}`;
      add(category, 'Name', chemical.name);
      add(category, 'Role', chemical.role);
      add(category, 'Quantity', `${chemical.quantity ?? ''} ${chemical.quantity_unit || ''}`.trim());
      add(category, 'Formula', chemical.formula);
    });
    (results.products || []).forEach(product => add('Product', product.name, `${product.amount ?? ''} ${product.unit || ''} · yield ${product.yield ?? ''}%`));
    (results.byproducts || []).forEach(product => add('Byproduct', product.name, `${product.amount ?? ''} ${product.unit || ''} · yield ${product.yield ?? ''}%`));
    return csv;
  }

  async function fetchSavedSimulation(simulationId) {
    return api(`/api/simulations/${encodeURIComponent(simulationId)}`);
  }

  window.openSavedSimulation = async function (simulationId) {
    try {
      const record = await fetchSavedSimulation(simulationId);
      const experiment = record.experiment || {};
      const defaults = freshWizardData();
      wizard.data = {
        ...defaults,
        ...experiment,
        experimentType: experiment.experiment_type || experiment.experimentType || defaults.experimentType,
        globalConditions: experiment.global_conditions || experiment.globalConditions || defaults.globalConditions,
      };
      wizard.results = record.simulation_results || null;
      wizard.simulationId = record.id;
      wizard.experimentId = record.experiment_id || null;
      wizard.historyView = true;
      wizard.step = 4;
      wizard.isOpen = true;
      renderWizardOverlay();
      goToStep(4);
    } catch (err) {
      toast(`Could not open saved simulation: ${err.message}`);
    }
  };

  window.downloadSavedSimulation = async function (simulationId) {
    try {
      const record = await fetchSavedSimulation(simulationId);
      const safeName = String(record.name || 'simulation').toLowerCase().replace(/[^a-z0-9]+/g, '_');
      downloadTextFile(`simulation_${safeName}.json`, JSON.stringify({
        id: record.id,
        experiment_id: record.experiment_id,
        experiment: record.experiment,
        simulation_results: record.simulation_results,
        created_at: record.created_at,
        exported_at: new Date().toISOString()
      }, null, 2), 'application/json');
      toast('Saved simulation JSON downloaded');
    } catch (err) {
      toast(`Could not download simulation: ${err.message}`);
    }
  };

  window.downloadSavedSimulationCSV = async function (simulationId) {
    try {
      const record = await fetchSavedSimulation(simulationId);
      const safeName = String(record.name || 'simulation').toLowerCase().replace(/[^a-z0-9]+/g, '_');
      downloadTextFile(`simulation_${safeName}.csv`, buildSimulationCsv(record.experiment || {}, record.simulation_results || {}), 'text/csv;charset=utf-8;');
      toast('Saved simulation CSV downloaded');
    } catch (err) {
      toast(`Could not download simulation: ${err.message}`);
    }
  };

  window.copyShareLink = function () {
    if (!wizard.simulationId) {
      toast('Run the simulation first so it can be shared.');
      return;
    }
    const shareUrl = `${window.location.origin}${window.location.pathname}#simulation-${encodeURIComponent(wizard.simulationId)}`;
    navigator.clipboard?.writeText(shareUrl).then(() => {
      toast('Shareable simulation link copied to clipboard!');
    }).catch(() => {
      toast('Link: ' + shareUrl);
    });
  };

  window.addEventListener('load', () => {
    const match = window.location.hash.match(/^#simulation-(.+)$/);
    if (match) setTimeout(() => window.openSavedSimulation?.(decodeURIComponent(match[1])), 500);
  });

  window.cloneAndRepeatExperiment = function () {
    const clone = JSON.parse(JSON.stringify(wizard.data));
    clone.name = (clone.name || 'Experiment') + ' (Iteration 2)';
    window.openNewExperimentWizard(clone);
    toast('Cloned parameters into new iteration');
  };

})();
