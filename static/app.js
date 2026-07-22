const scanBtn = document.getElementById('scanBtn');
const notConfigured = document.getElementById('notConfigured');
const consoleEl = document.getElementById('console');
const consoleLine = document.getElementById('consoleLine');
const errorCard = document.getElementById('errorCard');
const errorMessage = document.getElementById('errorMessage');
const results = document.getElementById('results');
const connDot = document.getElementById('connDot');
const connLabel = document.getElementById('connLabel');

function mbLink(mbid) {
  return mbid ? `https://musicbrainz.org/artist/${mbid}` : null;
}

function lastfmLink(name) {
  return `https://www.last.fm/music/${encodeURIComponent(name.replace(/ /g, '+'))}`;
}

async function checkHealth() {
  try {
    const res = await fetch('/api/health');
    const data = await res.json();
    if (data.configured) {
      connDot.classList.add('ok');
      connLabel.textContent = data.navidrome_url;
      scanBtn.disabled = false;
    } else {
      connDot.classList.add('bad');
      connLabel.textContent = 'not configured';
      notConfigured.classList.remove('hidden');
      scanBtn.disabled = true;
    }
  } catch (e) {
    connDot.classList.add('bad');
    connLabel.textContent = 'unreachable';
  }
}

function showError(message) {
  consoleEl.classList.add('hidden');
  errorMessage.textContent = message;
  errorCard.classList.remove('hidden');
  scanBtn.disabled = false;
  scanBtn.textContent = 'Try again';
}

function renderGenres(genres) {
  const meter = document.getElementById('genreMeter');
  meter.innerHTML = '';
  if (!genres.length) {
    meter.innerHTML = '<p class="empty-note">No genre tags found in your library yet.</p>';
    return;
  }
  const max = Math.max(...genres.map(g => g.songCount));
  genres.forEach(g => {
    const row = document.createElement('div');
    row.className = 'meter-row';
    row.innerHTML = `
      <span class="meter-name">${g.name}</span>
      <span class="meter-track"><span class="meter-fill" data-pct="${(g.songCount / max) * 100}"></span></span>
      <span class="meter-count">${g.songCount}</span>
    `;
    meter.appendChild(row);
  });
  // animate after insertion
  requestAnimationFrame(() => {
    meter.querySelectorAll('.meter-fill').forEach(el => {
      el.style.width = el.dataset.pct + '%';
    });
  });
}

function renderSeeds(seeds) {
  const row = document.getElementById('seedChips');
  row.innerHTML = '';
  seeds.forEach(name => {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = name;
    row.appendChild(chip);
  });
}

function renderRecommendations(recs) {
  const grid = document.getElementById('recGrid');
  grid.innerHTML = '';
  if (!recs.length) {
    grid.innerHTML = '<p class="empty-note">No recommendations turned up this time — your library may already cover the artists closest to your taste, or MusicBrainz/ListenBrainz didn\'t recognize enough of your seed artists. Try again later.</p>';
    return;
  }
  recs.forEach(rec => {
    const card = document.createElement('div');
    card.className = 'rec-card';
    const mb = mbLink(rec.mbid);
    card.innerHTML = `
      <div class="rec-card-top">
        <span class="rec-name">${rec.name}</span>
        <span class="rec-match">${rec.match_pct}% match</span>
      </div>
      <div class="rec-because">because you play ${rec.because_of.join(', ')}</div>
      <div class="rec-links">
        ${mb ? `<a href="${mb}" target="_blank" rel="noopener">MusicBrainz</a>` : ''}
        <a href="${lastfmLink(rec.name)}" target="_blank" rel="noopener">Last.fm</a>
      </div>
    `;
    grid.appendChild(card);
  });
}

async function pollJob(jobId) {
  const res = await fetch(`/api/scan/${jobId}`);
  if (!res.ok) throw new Error('Lost track of the scan job.');
  const job = await res.json();

  consoleLine.textContent = `> ${job.message}`;

  if (job.status === 'running') {
    setTimeout(() => pollJob(jobId), 900);
    return;
  }
  if (job.status === 'error') {
    showError(job.error || 'The scan failed for an unknown reason.');
    return;
  }

  // done
  consoleEl.classList.add('hidden');
  const data = job.result;
  document.getElementById('statArtistCount').textContent = data.library_artist_count;
  document.getElementById('statRecCount').textContent = data.recommendations.length;
  renderGenres(data.top_genres);
  renderSeeds(data.seed_artists);
  renderRecommendations(data.recommendations);
  results.classList.remove('hidden');
  scanBtn.disabled = false;
  scanBtn.textContent = 'Analyze again';
}

async function startScan() {
  scanBtn.disabled = true;
  scanBtn.textContent = 'Working...';
  errorCard.classList.add('hidden');
  results.classList.add('hidden');
  consoleEl.classList.remove('hidden');
  consoleLine.textContent = '> starting up...';

  try {
    const res = await fetch('/api/scan', { method: 'POST' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Could not start the scan.');
    }
    const { job_id } = await res.json();
    pollJob(job_id);
  } catch (e) {
    showError(e.message);
  }
}

scanBtn.addEventListener('click', startScan);
scanBtn.disabled = true;
checkHealth();
