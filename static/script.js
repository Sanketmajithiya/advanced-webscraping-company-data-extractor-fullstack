

// Initialize areas from localStorage or default to 'Adajan'
let savedAreas = localStorage.getItem('targetAreas');
let areas = savedAreas ? JSON.parse(savedAreas) : ['Adajan'];
let currentMode = 'it';

document.addEventListener('DOMContentLoaded', () => {
    updateAreaTags();
    pollStatus();

    // --- Interactive Effects ---
    const cursor = document.querySelector('.cursor-glow');
    const panel = document.querySelector('.control-panel');
    const buttons = document.querySelectorAll('.run-btn, .toggle-btn, .action-btn');

    document.addEventListener('mousemove', (e) => {
        // Cursor Glow
        if (cursor) {
            cursor.style.left = e.clientX + 'px';
            cursor.style.top = e.clientY + 'px';
        }

        // 3D Tilt for Control Panel
        if (panel) {
            const rect = panel.getBoundingClientRect();
            const x = e.clientX - rect.left; // x position within the element.
            const y = e.clientY - rect.top;  // y position within the element.

            const centerX = rect.width / 2;
            const centerY = rect.height / 2;

            const rotateX = ((y - centerY) / centerY) * -2; // Max rotation deg
            const rotateY = ((x - centerX) / centerX) * 2;

            if (x >= 0 && x <= rect.width && y >= 0 && y <= rect.height) {
                panel.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg)`;
            } else {
                panel.style.transform = `perspective(1000px) rotateX(0deg) rotateY(0deg)`;
            }
        }
    });

    // Magnetic Buttons
    buttons.forEach(btn => {
        btn.addEventListener('mousemove', (e) => {
            const rect = btn.getBoundingClientRect();
            const x = e.clientX - rect.left - rect.width / 2;
            const y = e.clientY - rect.top - rect.height / 2;
            btn.style.transform = `translate(${x * 0.2}px, ${y * 0.2}px)`;
        });

        btn.addEventListener('mouseleave', () => {
            btn.style.transform = 'translate(0, 0)';
        });
    });
});

function setMode(mode) {
    currentMode = mode;
    document.querySelectorAll('.toggle-btn').forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.mode === mode) btn.classList.add('active');
    });

    const customInput = document.getElementById('customQueryGroup');
    if (mode === 'custom') {
        customInput.style.display = 'block';
    } else {
        customInput.style.display = 'none';
    }
}

function saveAreas() {
    localStorage.setItem('targetAreas', JSON.stringify(areas));
}

function addArea() {
    const input = document.getElementById('areaInput');
    const area = input.value.trim();

    if (area && !areas.includes(area)) {
        areas.push(area);
        saveAreas(); // Save to localStorage
        updateAreaTags();
        input.value = '';
    }
}

function removeArea(element) {
    const area = element.parentElement.textContent.trim();
    areas = areas.filter(a => a !== area);
    saveAreas(); // Save to localStorage
    updateAreaTags();
}

function updateAreaTags() {
    const container = document.getElementById('areaTags');
    container.innerHTML = '';

    areas.forEach(area => {
        const tag = document.createElement('div');
        tag.className = 'tag';
        tag.innerHTML = `${area} <i class="fa-solid fa-xmark" onclick="removeArea(this)"></i>`;
        container.appendChild(tag);
    });
}


async function startScraper() {
    const city = document.getElementById('cityInput').value;
    const customQuery = document.getElementById('customQueryInput').value;

    if (areas.length === 0) {
        alert('Please add at least one area!');
        return;
    }

    // Request Notification Permission
    if ("Notification" in window && Notification.permission !== "granted") {
        await Notification.requestPermission();
    }

    const payload = {
        city: city,
        areas: areas,
        category: currentMode,
        custom_query: currentMode === 'custom' ? customQuery : ''
    };

    document.getElementById('loaderOverlay').style.display = 'flex';

    try {
        const response = await fetch('/api/scrape', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (data.status === 'success') {
            sendNotification("Scraper Started 🚀", "You can minimize this tab. The process will run in the background.");
            pollStatus();
        } else {
            alert('Error: ' + data.message);
            document.getElementById('loaderOverlay').style.display = 'none';
        }
    } catch (e) {
        alert('Request failed: ' + e);
        document.getElementById('loaderOverlay').style.display = 'none';
    }
}

let pollingInterval = null;

function pollStatus() {
    if (pollingInterval) clearInterval(pollingInterval);


    pollingInterval = setInterval(async () => {
        try {
            const res = await fetch('/api/status?t=' + new Date().getTime());
            const data = await res.json();

            if (data.is_scraping) {
                // Show overlay if not already visible
                const overlay = document.getElementById('loaderOverlay');
                if (overlay.style.display === 'none' || overlay.style.display === '') {
                    overlay.style.display = 'flex';
                }

                document.getElementById('statusIndicator').innerHTML =
                    `<span class="dot" style="background: orange; box-shadow: 0 0 10px orange;"></span> Running...`;

                // Update Progress UI
                if (data.progress) {
                    const progress = data.progress;
                    const total = progress.total || 100; // avoid div by zero
                    const processed = progress.processed || 0;
                    const percent = Math.min(100, Math.round((processed / total) * 100));

                    document.getElementById('progress-fill').style.width = `${percent}%`;
                    document.getElementById('status-text').innerText = `${progress.current_area} | ${processed}/${total}`;

                    // Update Logs
                    const consoleDiv = document.getElementById('log-console');
                    if (progress.log && progress.log.length > 0) {
                        // Handle formatting and Popups
                        const cleanedLogs = [];

                        // Check for new logs to trigger popup
                        if (progress.log.length > (window.lastLogCount || 0)) {
                            const newLogs = progress.log.slice(window.lastLogCount || 0);
                            newLogs.forEach(l => {
                                if (l.startsWith('[POPUP]')) {
                                    showToast(l.replace('[POPUP]', '').trim());
                                }
                            });
                            window.lastLogCount = progress.log.length;
                        }

                        // Prepare logs for console (remove [POPUP] tag for cleaner look)
                        progress.log.forEach(l => {
                            cleanedLogs.push(l.replace('[POPUP]', ''));
                        });

                        consoleDiv.innerHTML = cleanedLogs.map(l => `<div class="log-entry">${l}</div>`).join('');
                        consoleDiv.scrollTop = consoleDiv.scrollHeight;
                    }
                }

            } else {
                document.getElementById('loaderOverlay').style.display = 'none';
                document.getElementById('statusIndicator').innerHTML =
                    `<span class="dot"></span> System Ready`;

                // START: Robust Result Logic
                // If there is a file, we should show it, regardless of whether status is "Completed" or "Idle"
                if (data.latest_file) {
                    // Stop polling if we found a file
                    if (pollingInterval) {
                        clearInterval(pollingInterval);
                        pollingInterval = null;
                    }

                    // Only trigger multiple times if we haven't shown it yet (check local var or UI state)
                    // We check if the panel meets specific visibility or if we tracked the filename
                    if (window.latestFilename !== data.latest_file) {
                        sendNotification("Scraping Completed! ✅", "Your data is ready. Click to view results.");
                        showResults(data.latest_file);
                        document.getElementById('progress-fill').style.width = `100%`;
                        document.getElementById('status-text').innerText = "Completed!";
                    }
                }
                // Status says completed but NO file (e.g. 0 results found case)
                else if (data.progress.status === "Completed" && !data.latest_file) {
                    clearInterval(pollingInterval);
                    pollingInterval = null;
                    alert("Scraping finished but no data was collected. 📉\nTry a different area or keyword.");
                    document.getElementById('status-text').innerText = "Completed (No Data)";
                }
                // END: Robust Result Logic
            }
        } catch (e) {
            console.error(e);
        }
    }, 1000);

}

function showResults(filename) {
    console.log("Showing results for:", filename);
    const panel = document.getElementById('resultsPanel');
    panel.style.display = 'block';

    // REMOVED: Scroll to results to ensure visibility on mobile (Caused annoyance on refresh)
    // panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

    window.latestFilename = filename;
}

function downloadData() {
    if (window.latestFilename) {
        window.location.href = `/api/download/${window.latestFilename}`;
    } else {
        alert("No file generated yet! Please start extraction first.");
    }
}

async function viewData() {
    const container = document.getElementById('dataPreview');
    // FIX: Check if TABLE exists to toggle. If checking innerHTML != '', it fails because of the initial empty-state text.
    if (container.querySelector('table')) {
        closeResults();
        return;
    }

    if (!window.latestFilename) {
        alert("No file generated yet! Please start extraction first.");
        return;
    }

    try {
        const res = await fetch(`/api/view/${window.latestFilename}`);
        const result = await res.json();

        if (result.status === 'success') {
            const container = document.getElementById('dataPreview');

            if (result.data.length === 0) {
                container.innerHTML = '<p>No data found in the file.</p>';
                return;
            }

            let html = '<div style="overflow-x: auto;"><table><thead><tr>';

            // Limit columns for preview
            const previewCols = ['Company Name', 'Area', 'Phone (Maps)', 'Website', 'Email (Website)'];

            previewCols.forEach(col => {
                html += `<th>${col}</th>`;
            });
            html += '</tr></thead><tbody>';

            result.data.slice(0, 10).forEach(row => {
                html += '<tr>';
                previewCols.forEach(col => {
                    let maxChars = 30; // Truncate long text
                    let content = row[col] || '-';
                    let displayContent = content.length > maxChars ? content.substring(0, maxChars) + '...' : content;

                    if (col === 'Website' && content !== '-' && content !== 'Not Found') {
                        let url = content.startsWith('http') ? content : 'http://' + content;
                        html += `<td><a href="${url}" target="_blank" style="color: #4CAF50; text-decoration: underline;">${displayContent}</a></td>`;
                    } else if (col === 'Email (Website)' && content !== '-' && content !== 'Not Found') {
                        html += `<td><a href="mailto:${content.split(',')[0].trim()}" style="color: #4CAF50; text-decoration: underline;">${displayContent}</a></td>`;
                    } else {
                        html += `<td>${displayContent}</td>`;
                    }
                });
                html += '</tr>';
            });

            html += '</tbody></table></div>';
            if (result.data.length > 10) {
                html += `<p style="text-align: center; margin-top: 10px; color: #888;">Showing first 10 of ${result.data.length} records. Download full Excel for more.</p>`;
            }

            container.innerHTML = html;
        }
    } catch (e) {
        alert('Error viewing data: ' + e);
    }
}

function closeResults() {
    const container = document.getElementById('dataPreview');
    // Restore the empty state HTML immediately
    container.innerHTML = `
        <div class="empty-state">
            <i class="fa-solid fa-database"></i>
            <p>No data loaded yet. Click "View Data" after extraction.</p>
        </div>
    `;
}

function sendNotification(title, body) {
    if ("Notification" in window && Notification.permission === "granted") {
        new Notification(title, {
            body: body,
            icon: '/static/favicon.ico'
        });
    }
}

function showToast(message) {
    const toast = document.getElementById('toast-notification');
    if (toast) {
        toast.innerText = message;
        toast.classList.remove('hidden');
        // Small delay to allow display:block to apply before adding show class for transition
        setTimeout(() => toast.classList.add('show'), 10);

        // Hide after 8 seconds
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.classList.add('hidden'), 500); // Wait for transition
        }, 8000);
    }
}
