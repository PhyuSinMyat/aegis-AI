document.addEventListener('DOMContentLoaded', function () {

    // ═══════════════════════════
    // STATE
    // ═══════════════════════════
    let currentStep        = 0;
    const TOTAL_STEPS      = 4; // 0-3
    let moduleFilesStore   = [];
    let occupiedTimesStore = [];
    let studyAppsStore     = [];

    // ═══════════════════════════
    // ELEMENT REFS
    // ═══════════════════════════
    const generateBtn        = document.getElementById('generateBtn');
    const progressFill       = document.getElementById('wizardProgressFill');

    const classInput         = document.getElementById('class_timetable');
    const moduleInput        = document.getElementById('module_timetables');
    const classFileList      = document.getElementById('classFileList');
    const modulePreviewBody  = document.getElementById('modulePreviewBody');
    const modulePreviewGroup = document.getElementById('modulePreviewGroup');
    const hiddenModuleFields = document.getElementById('hiddenModuleFields');

    const semesterStartDate  = document.getElementById('semester_start_date');
    const semesterEndDate    = document.getElementById('semester_end_date');
    const preferredStudyTime = document.getElementById('preferred_study_time');
    const sessionLength      = document.getElementById('session_length');

    const otTitle            = document.getElementById('ot_title');
    const otCategory         = document.getElementById('ot_category');
    const otDay              = document.getElementById('ot_day');
    const otStart            = document.getElementById('ot_start');
    const otEnd              = document.getElementById('ot_end');
    const otNotes            = document.getElementById('ot_notes');
    const occupiedList       = document.getElementById('occupiedList');
    const occupiedEmptyState = document.getElementById('occupiedEmptyState');
    const hiddenOccupiedFields = document.getElementById('hiddenOccupiedFields');

    const studyAppName       = document.getElementById('study_app_name');
    const studyAppType       = document.getElementById('study_app_type');
    const studyAppIdentifier = document.getElementById('study_app_identifier');
    const studyAppPurpose    = document.getElementById('study_app_purpose');
    const selectedAppsTray   = document.getElementById('selectedAppsTray');
    const trayPlaceholder    = document.getElementById('trayPlaceholder');
    const appCountBadge      = document.getElementById('appCountBadge');
    const hiddenStudyAppFields = document.getElementById('hiddenStudyAppFields');

    // ═══════════════════════════
    // HELPERS
    // ═══════════════════════════
    function esc(str) {
        return String(str ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }
    function norm(id) { return String(id ?? '').trim().toLowerCase(); }
    function typeLabel(t) { return {desktop:'Desktop App',website:'Website',both:'Both'}[t] ?? t; }
    function fmtTime(t) {
        if (!t) return '';
        var parts = t.split(':');
        var hr = parseInt(parts[0]);
        return (hr % 12 || 12) + ':' + parts[1] + ' ' + (hr >= 12 ? 'PM' : 'AM');
    }
    function showErr(id, msg) { var e = document.getElementById(id); if (e) e.textContent = msg; }
    function clrErr(id)       { var e = document.getElementById(id); if (e) e.textContent = ''; }
    function highlightBorder(el, bad) {
        el.style.borderColor = bad ? '#dc2626' : '';
        el.style.boxShadow   = bad ? '0 0 0 3px rgba(220,38,38,0.12)' : '';
    }

    // ═══════════════════════════
    // WIZARD NAVIGATION (4 steps: 0-3)
    // ═══════════════════════════
    function goTo(n, skipValidation) {
        if (!skipValidation && n > currentStep) {
            if (!validateStep(currentStep)) return;
        }
        var panels = document.querySelectorAll('.wz-panel');
        var steps  = document.querySelectorAll('.wz-step');

        panels.forEach(function(p, i) { p.classList.toggle('active', i === n); });

        steps.forEach(function(s, i) {
            s.classList.remove('active', 'completed');
            if (i < n)       s.classList.add('completed');
            else if (i === n) s.classList.add('active');
        });

        for (var i = 0; i < TOTAL_STEPS - 1; i++) {
            var c = document.getElementById('conn-' + i + '-' + (i + 1));
            if (c) c.classList.toggle('completed', i < n);
        }

        progressFill.style.width = (n / (TOTAL_STEPS - 1) * 100) + '%';
        if (n === 3) buildReview();
        currentStep = n;

        document.getElementById('studyPlanForm')
            .scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    // Nav buttons
    document.getElementById('next0').addEventListener('click', function() { goTo(1); });
    document.getElementById('back1').addEventListener('click', function() { goTo(0, true); });
    document.getElementById('next1').addEventListener('click', function() { goTo(2); });
    document.getElementById('back2').addEventListener('click', function() { goTo(1, true); });
    document.getElementById('next2').addEventListener('click', function() { goTo(3); });
    document.getElementById('skip2').addEventListener('click', function() { goTo(3, true); });
    document.getElementById('back3').addEventListener('click', function() { goTo(2, true); });

    // Review edit buttons
    document.getElementById('editFiles').addEventListener('click',       function() { goTo(0, true); });
    document.getElementById('editStyle').addEventListener('click',       function() { goTo(1, true); });
    document.getElementById('editCommitments').addEventListener('click', function() { goTo(2, true); });
    document.getElementById('editApps').addEventListener('click',        function() { goTo(1, true); });

    // Click completed step bubble to jump back
    document.querySelectorAll('.wz-step').forEach(function(s) {
        s.addEventListener('click', function() {
            var t = Number(s.dataset.step);
            if (t < currentStep) goTo(t, true);
        });
    });

    // ═══════════════════════════
    // STEP VALIDATION
    // ═══════════════════════════
    function validateStep(step) {
        if (step === 0) return validateStep0();
        if (step === 1) return validateStep1();
        return true;
    }

    function validateStep0() {
        var ok = true;
        if (!classInput.files.length) {
            showErr('classFileError', 'Please upload your class timetable.'); ok = false;
        } else clrErr('classFileError');

        if (!moduleFilesStore.length) {
            showErr('moduleFileError', 'Please upload at least one module timetable.'); ok = false;
        } else clrErr('moduleFileError');

        if (!semesterStartDate.value) {
            highlightBorder(semesterStartDate, true); ok = false;
        } else highlightBorder(semesterStartDate, false);

        if (!semesterEndDate.value) {
            highlightBorder(semesterEndDate, true); ok = false;
        } else if (semesterStartDate.value && semesterEndDate.value <= semesterStartDate.value) {
            highlightBorder(semesterEndDate, true);
            showErr('classFileError', 'End date must be after start date.'); ok = false;
        } else highlightBorder(semesterEndDate, false);

        return ok;
    }

    function validateStep1() {
        var ok = true;
        if (!document.querySelector('input[name="study_intensity"]:checked')) {
            showErr('intensityError', 'Please select a study intensity.'); ok = false;
        } else clrErr('intensityError');

        if (!preferredStudyTime.value) {
            highlightBorder(preferredStudyTime, true); ok = false;
        } else highlightBorder(preferredStudyTime, false);

        if (!sessionLength.value) {
            highlightBorder(sessionLength, true); ok = false;
        } else highlightBorder(sessionLength, false);

        if (!document.querySelector('input[name="break_preference"]:checked')) {
            showErr('breakError', 'Please select a break preference.'); ok = false;
        } else clrErr('breakError');

        if (!studyAppsStore.length) {
            showErr('studyAppError', 'Please select at least one study app.'); ok = false;
        } else clrErr('studyAppError');

        return ok;
    }

    function validateFinalForm() {
        var ok =
            classInput.files.length > 0 &&
            moduleFilesStore.length > 0 &&
            !!semesterStartDate.value &&
            !!semesterEndDate.value &&
            !!document.querySelector('input[name="study_intensity"]:checked') &&
            !!preferredStudyTime.value &&
            !!sessionLength.value &&
            !!document.querySelector('input[name="break_preference"]:checked') &&
            studyAppsStore.length > 0;
        generateBtn.disabled = !ok;
    }

    // ═══════════════════════════
    // DRAG & DROP
    // ═══════════════════════════
    function setupDragDrop(zoneId, input) {
        var zone = document.getElementById(zoneId);
        zone.addEventListener('dragover', function(e) { e.preventDefault(); zone.classList.add('dragover'); });
        zone.addEventListener('dragleave', function() { zone.classList.remove('dragover'); });
        zone.addEventListener('drop', function(e) {
            e.preventDefault(); zone.classList.remove('dragover');
            if (e.dataTransfer.files.length) {
                var dt = new DataTransfer();
                Array.from(e.dataTransfer.files).forEach(function(f) { dt.items.add(f); });
                input.files = dt.files;
                input.dispatchEvent(new Event('change'));
            }
        });
    }
    setupDragDrop('classDropZone', classInput);
    setupDragDrop('moduleDropZone', moduleInput);

    // ═══════════════════════════
    // CLASS TIMETABLE
    // ═══════════════════════════
    classInput.addEventListener('change', function() {
        classFileList.innerHTML = '';
        clrErr('classFileError');
        if (classInput.files.length) {
            var chip = document.createElement('div');
            chip.className = 'wz-file-chip';
            chip.innerHTML = '<i class="fa-solid fa-file"></i> ' + esc(classInput.files[0].name);
            classFileList.appendChild(chip);
        }
        validateFinalForm();
    });

    // ═══════════════════════════
    // MODULE FILES
    // ═══════════════════════════
    function parseMod(filename) {
        var base = filename.replace(/\.[^.]+$/, '');
        var sep  = base.indexOf('-');
        if (sep === -1) sep = base.indexOf('–'); // en-dash
        return sep !== -1
            ? { code: base.slice(0, sep).trim().toUpperCase(), name: base.slice(sep + 1).trim() }
            : { code: '', name: base.trim() };
    }
    function syncModuleInput() {
        var dt = new DataTransfer();
        moduleFilesStore.forEach(function(m) { dt.items.add(m.file); });
        moduleInput.files = dt.files;
    }
    function rebuildModuleHidden() {
        hiddenModuleFields.innerHTML = '';
        moduleFilesStore.forEach(function(m, i) {
            [['module_code_' + i, m.code], ['module_name_' + i, m.name]].forEach(function(pair) {
                var inp = document.createElement('input');
                inp.type = 'hidden'; inp.name = pair[0]; inp.value = pair[1];
                hiddenModuleFields.appendChild(inp);
            });
        });
    }
    function renderModulePreview() {
        modulePreviewBody.innerHTML = '';
        modulePreviewGroup.style.display = moduleFilesStore.length ? '' : 'none';
        if (!moduleFilesStore.length) return;

        moduleFilesStore.forEach(function(item, i) {
            var row = document.createElement('div');
            row.className = 'wz-module-row';
            row.innerHTML =
                '<span class="wz-mod-file"><i class="fa-solid fa-file"></i>' + esc(item.file.name) + '</span>' +
                '<input type="text" class="form-input mod-code" data-i="' + i + '" value="' + esc(item.code) + '" placeholder="Code" maxlength="20">' +
                '<input type="text" class="form-input mod-name" data-i="' + i + '" value="' + esc(item.name) + '" placeholder="Module name" maxlength="120">' +
                '<button type="button" class="wz-mod-del" data-i="' + i + '">&times;</button>';
            modulePreviewBody.appendChild(row);
        });

        rebuildModuleHidden();

        modulePreviewBody.querySelectorAll('.mod-code').forEach(function(inp) {
            inp.addEventListener('input', function() {
                moduleFilesStore[+this.dataset.i].code = this.value.toUpperCase();
                rebuildModuleHidden();
            });
        });
        modulePreviewBody.querySelectorAll('.mod-name').forEach(function(inp) {
            inp.addEventListener('input', function() {
                moduleFilesStore[+this.dataset.i].name = this.value;
                rebuildModuleHidden();
            });
        });
        modulePreviewBody.querySelectorAll('.wz-mod-del').forEach(function(btn) {
            btn.addEventListener('click', function() {
                moduleFilesStore.splice(+this.dataset.i, 1);
                syncModuleInput(); renderModulePreview(); validateFinalForm();
            });
        });
    }

    moduleInput.addEventListener('change', function() {
        clrErr('moduleFileError');
        var allowed = ['csv', 'xlsx', 'pdf'];
        Array.from(this.files).forEach(function(f) {
            var ext = f.name.split('.').pop().toLowerCase();
            if (!allowed.includes(ext)) { showErr('moduleFileError', '"' + f.name + '" is not supported.'); return; }
            if (!moduleFilesStore.some(function(m) { return m.file.name === f.name && m.file.size === f.size; })) {
                var p = parseMod(f.name);
                moduleFilesStore.push({ file: f, code: p.code, name: p.name });
            }
        });
        syncModuleInput(); renderModulePreview(); validateFinalForm();
    });

    // ═══════════════════════════
    // INTENSITY / BREAK CARDS
    // ═══════════════════════════
    document.querySelectorAll('.intensity-card').forEach(function(card) {
        card.addEventListener('click', function() {
            document.querySelectorAll('.intensity-card').forEach(function(c) { c.classList.remove('selected'); });
            card.classList.add('selected');
            clrErr('intensityError'); validateFinalForm();
        });
    });
    document.querySelectorAll('.break-card').forEach(function(card) {
        card.addEventListener('click', function() {
            document.querySelectorAll('.break-card').forEach(function(c) { c.classList.remove('selected'); });
            card.classList.add('selected');
            clrErr('breakError'); validateFinalForm();
        });
    });
    [semesterStartDate, semesterEndDate, preferredStudyTime, sessionLength]
        .forEach(function(el) { el.addEventListener('change', validateFinalForm); });

    // ═══════════════════════════
    // ACCORDION
    // ═══════════════════════════
    document.querySelectorAll('.acc-header').forEach(function(header) {
        header.addEventListener('click', function() {
            var body = this.nextElementSibling;
            var isOpen = body.classList.contains('open');
            body.classList.toggle('open', !isOpen);
            this.classList.toggle('open', !isOpen);
        });
    });

    // ═══════════════════════════
    // OCCUPIED TIMES
    // ═══════════════════════════
    var addCommitmentForm = document.getElementById('addCommitmentForm');
    var showAddBtn = document.getElementById('showAddCommitment');

    showAddBtn.addEventListener('click', function() {
        addCommitmentForm.style.display = addCommitmentForm.style.display === 'none' ? 'block' : 'none';
    });
    document.getElementById('cancelAddCommitment').addEventListener('click', function() {
        addCommitmentForm.style.display = 'none';
        otTitle.value = ''; otStart.value = ''; otEnd.value = ''; otNotes.value = '';
        document.getElementById('occupiedTimesError').textContent = '';
    });

    function renderOccupied() {
        document.querySelectorAll('.occupied-item').forEach(function(el) { el.remove(); });
        if (!occupiedTimesStore.length) {
            occupiedEmptyState.style.display = ''; return;
        }
        occupiedEmptyState.style.display = 'none';
        occupiedTimesStore.forEach(function(item, i) {
            var div = document.createElement('div');
            div.className = 'occupied-item';
            div.innerHTML =
                '<span class="occ-badge">' + esc(item.category) + '</span>' +
                '<span class="occ-title">' + esc(item.title) + '</span>' +
                '<span class="occ-time">' + esc(item.day_of_week) + ' &middot; ' + fmtTime(item.start_time) + ' – ' + fmtTime(item.end_time) + '</span>' +
                (item.notes ? '<span class="occ-notes" title="' + esc(item.notes) + '">' + esc(item.notes) + '</span>' : '') +
                '<button type="button" class="occ-del" data-i="' + i + '">&times;</button>';
            occupiedList.appendChild(div);
        });
        document.querySelectorAll('.occ-del').forEach(function(btn) {
            btn.addEventListener('click', function() {
                occupiedTimesStore.splice(+this.dataset.i, 1);
                renderOccupied(); rebuildOccupiedHidden();
            });
        });
        rebuildOccupiedHidden();
    }

    function rebuildOccupiedHidden() {
        hiddenOccupiedFields.innerHTML = '';
        occupiedTimesStore.forEach(function(item, i) {
            [['occupied_title_' + i, item.title], ['occupied_category_' + i, item.category],
             ['occupied_day_' + i, item.day_of_week], ['occupied_start_' + i, item.start_time],
             ['occupied_end_' + i, item.end_time], ['occupied_notes_' + i, item.notes]
            ].forEach(function(pair) {
                var inp = document.createElement('input');
                inp.type = 'hidden'; inp.name = pair[0]; inp.value = pair[1] ?? '';
                hiddenOccupiedFields.appendChild(inp);
            });
        });
        var c = document.createElement('input');
        c.type = 'hidden'; c.name = 'occupied_count'; c.value = occupiedTimesStore.length;
        hiddenOccupiedFields.appendChild(c);
    }

    document.getElementById('addOccupiedBtn').addEventListener('click', function() {
        var err = document.getElementById('occupiedTimesError');
        err.textContent = '';
        var title = otTitle.value.trim(), start = otStart.value, end = otEnd.value;
        if (!title)       { err.textContent = 'Please enter a title.'; return; }
        if (!start)       { err.textContent = 'Please enter a start time.'; return; }
        if (!end)         { err.textContent = 'Please enter an end time.'; return; }
        if (start >= end) { err.textContent = 'End time must be after start time.'; return; }
        occupiedTimesStore.push({
            title: title, category: otCategory.value, day_of_week: otDay.value,
            start_time: start, end_time: end, notes: otNotes.value.trim()
        });
        renderOccupied();
        otTitle.value = ''; otNotes.value = ''; otStart.value = ''; otEnd.value = '';
        addCommitmentForm.style.display = 'none';
    });

    // ═══════════════════════════
    // STUDY APPS
    // ═══════════════════════════
    var manualAppForm = document.getElementById('manualAppForm');
    document.getElementById('toggleManualApp').addEventListener('click', function() {
        manualAppForm.style.display = manualAppForm.style.display === 'none' ? 'block' : 'none';
    });

    function rebuildAppHidden() {
        hiddenStudyAppFields.innerHTML = '';
        studyAppsStore.forEach(function(a, i) {
            [['study_app_name_' + i, a.name], ['study_app_type_' + i, a.type],
             ['study_app_identifier_' + i, a.identifier], ['study_app_purpose_' + i, a.purpose]
            ].forEach(function(pair) {
                var inp = document.createElement('input');
                inp.type = 'hidden'; inp.name = pair[0]; inp.value = pair[1];
                hiddenStudyAppFields.appendChild(inp);
            });
        });
        var c = document.createElement('input');
        c.type = 'hidden'; c.name = 'study_app_count'; c.value = studyAppsStore.length;
        hiddenStudyAppFields.appendChild(c);
    }

    function renderAppTray() {
        document.querySelectorAll('.sel-app-tag').forEach(function(t) { t.remove(); });
        if (!studyAppsStore.length) {
            trayPlaceholder.style.display = '';
            appCountBadge.style.display   = 'none';
        } else {
            trayPlaceholder.style.display = 'none';
            appCountBadge.style.display   = '';
            appCountBadge.textContent     = studyAppsStore.length;
            studyAppsStore.forEach(function(a) {
                var tag = document.createElement('div');
                tag.className = 'sel-app-tag';
                tag.dataset.id = a.identifier;
                tag.innerHTML = esc(a.name) + ' <button type="button" class="tag-remove">&times;</button>';
                tag.querySelector('.tag-remove').addEventListener('click', function() { removeApp(a.identifier); });
                selectedAppsTray.appendChild(tag);
            });
        }
        rebuildAppHidden(); validateFinalForm();
    }

    function addApp(app) {
        var name = (app.name || '').trim(), id = (app.identifier || '').trim();
        if (!name || !id) return;
        var err = document.getElementById('studyAppError');
        if (studyAppsStore.some(function(a) { return norm(a.identifier) === norm(id); })) {
            err.textContent = '"' + name + '" is already in the list.'; return;
        }
        err.textContent = '';
        studyAppsStore.push({
            name: name, type: (app.type || 'desktop').trim(),
            typeLabel: typeLabel((app.type || 'desktop').trim()),
            identifier: id, purpose: (app.purpose || '').trim()
        });
        renderAppTray();
    }

    function removeApp(identifier) {
        studyAppsStore = studyAppsStore.filter(function(a) { return norm(a.identifier) !== norm(identifier); });
        document.querySelectorAll('.app-chip input[type="checkbox"]').forEach(function(cb) {
            if (norm(cb.dataset.identifier) === norm(identifier)) {
                cb.checked = false;
                cb.closest('.app-chip').classList.remove('checked');
            }
        });
        renderAppTray();
    }

    // Chip clicks
    document.querySelectorAll('.app-chip').forEach(function(chip) {
        chip.addEventListener('click', function() {
            var cb = this.querySelector('input[type="checkbox"]');
            cb.checked = !cb.checked;
            this.classList.toggle('checked', cb.checked);
            if (cb.checked) {
                addApp({ name: cb.dataset.name, type: cb.dataset.type,
                         identifier: cb.dataset.identifier, purpose: cb.dataset.purpose });
            } else {
                removeApp(cb.dataset.identifier);
            }
        });
    });

    // Manual add
    document.getElementById('addStudyAppBtn').addEventListener('click', function() {
        var err = document.getElementById('studyAppError');
        var name = studyAppName.value.trim(), id = studyAppIdentifier.value.trim();
        if (!name || !id) { err.textContent = 'Please enter an app name and tracking match value.'; return; }
        addApp({ name: name, type: studyAppType.value, identifier: id, purpose: studyAppPurpose.value });
        if (!err.textContent) {
            studyAppName.value = ''; studyAppIdentifier.value = ''; studyAppPurpose.value = '';
        }
    });
    document.getElementById('clearStudyAppInputsBtn').addEventListener('click', function() {
        studyAppName.value = ''; studyAppIdentifier.value = ''; studyAppPurpose.value = '';
        document.getElementById('studyAppError').textContent = '';
    });

    // ═══════════════════════════
    // REVIEW BUILDER
    // ═══════════════════════════
    var INTENSITY_MAP = {
        relaxed:   'Just Keeping Up (~10–15 hrs/week)',
        balanced:  'Steady Progress (~16–25 hrs/week)',
        focused:   'High Effort (~26–35 hrs/week)',
        intensive: 'Full Grind (~36+ hrs/week)'
    };
    var BREAK_MAP = { short: 'Short (5 min)', medium: 'Medium (10–15 min)', long: 'Long (20–30 min)' };

    function rr(key, val) {
        return '<div class="wz-rv-row"><span class="wz-rv-key">' + esc(key) + '</span><span class="wz-rv-val">' + val + '</span></div>';
    }

    function buildReview() {
        var classFile = classInput.files.length ? classInput.files[0].name : null;

        document.getElementById('reviewFilesBody').innerHTML =
            rr('Class timetable', classFile
                ? '<span class="wz-rv-val ok">' + esc(classFile) + '</span>'
                : '<span style="color:#dc2626">Not uploaded</span>') +
            rr('Module files', moduleFilesStore.length
                ? moduleFilesStore.length + ' file' + (moduleFilesStore.length !== 1 ? 's' : '')
                : '<span style="color:#dc2626">None</span>') +
            rr('Semester', esc(semesterStartDate.value || '—') + ' &rarr; ' + esc(semesterEndDate.value || '—'));

        var intVal   = (document.querySelector('input[name="study_intensity"]:checked') || {}).value;
        var breakVal = (document.querySelector('input[name="break_preference"]:checked') || {}).value;
        var selSess  = sessionLength.options[sessionLength.selectedIndex];
        var selTime  = preferredStudyTime.options[preferredStudyTime.selectedIndex];

        document.getElementById('reviewStyleBody').innerHTML =
            rr('Intensity',  esc(INTENSITY_MAP[intVal] || '—')) +
            rr('Study time', esc(selTime && selTime.value ? selTime.text : '—')) +
            rr('Session',    esc(selSess && selSess.value ? selSess.text : '—')) +
            rr('Break',      esc(BREAK_MAP[breakVal] || '—'));

        document.getElementById('reviewCommitmentsBody').innerHTML = occupiedTimesStore.length
            ? occupiedTimesStore.map(function(it) {
                return '<div class="wz-rv-commitment">' +
                    '<div class="wz-rv-comm-title">' + esc(it.title) + '</div>' +
                    '<div class="wz-rv-comm-meta">' + esc(it.day_of_week) + ' &middot; ' + fmtTime(it.start_time) + ' – ' + fmtTime(it.end_time) +
                    (it.notes ? ' &middot; ' + esc(it.notes) : '') + '</div></div>';
              }).join('')
            : '<p class="wz-rv-empty">No recurring commitments added.</p>';

        document.getElementById('reviewAppsBody').innerHTML = studyAppsStore.length
            ? '<div class="wz-rv-tags">' +
                studyAppsStore.map(function(a) { return '<span class="wz-rv-tag">' + esc(a.name) + '</span>'; }).join('') +
              '</div>'
            : '<p class="wz-rv-empty">No study apps selected.</p>';

        validateFinalForm();
    }

    // ═══════════════════════════
    // INIT
    // ═══════════════════════════
    renderOccupied();
    renderAppTray();
    validateFinalForm();

    // ═══════════════════════════
    // FORM SUBMISSION
    // ═══════════════════════════
    var submitting = false;
    var analysisStages = [
        'Validating documents…',
        'Extracting timetable and module information…',
        'Preparing extracted results…'
    ];

    document.getElementById('studyPlanForm').addEventListener('submit', function(e) {
        if (submitting) { e.preventDefault(); return; }
        submitting = true;
        generateBtn.disabled = true;

        var fileList = document.getElementById('analysisFileList');
        fileList.innerHTML = '';
        if (classInput.files.length) {
            fileList.innerHTML += '<span class="aegis-file-pill"><i class="fa-solid fa-file-lines"></i><span>' + esc(classInput.files[0].name) + '</span></span>';
        }
        moduleFilesStore.forEach(function(m) {
            fileList.innerHTML += '<span class="aegis-file-pill"><i class="fa-solid fa-file-lines"></i><span>' + esc(m.file.name) + '</span></span>';
        });

        document.getElementById('processingModalOverlay').style.display = 'flex';

        var stageIdx = 0;
        var statusText = document.getElementById('analysisStatusText');
        setInterval(function() {
            stageIdx = (stageIdx + 1) % analysisStages.length;
            statusText.style.opacity = '0';
            setTimeout(function() {
                statusText.textContent = analysisStages[stageIdx];
                statusText.style.opacity = '1';
            }, 300);
        }, 4000);

        setTimeout(function() {
            var hint = document.getElementById('analysisHint');
            if (hint) hint.innerHTML = '<i class="fa-solid fa-shield-halved"></i> Still working — larger documents take a bit longer.';
        }, 90000);
    });

    // ═══════════════════════════
    // TOGGLE WIZARD VISIBILITY
    // ═══════════════════════════
    var showWizardBtn = document.getElementById('showUploadWizardBtn');
    if (showWizardBtn) {
        showWizardBtn.addEventListener('click', function() {
            var summary = document.getElementById('detailedSavedSummary');
            var wizard  = document.getElementById('uploadWizardContainer');
            if (summary) summary.style.display = 'none';
            if (wizard) {
                wizard.style.display = 'block';
                wizard.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
    }
});
