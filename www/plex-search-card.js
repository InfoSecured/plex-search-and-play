/**
 * Plex Search and Play Card
 * A custom Lovelace card for searching and playing Plex media
 *
 * @version 1.0.0
 */

class PlexSearchCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._hass = null;
    this._results = [];
    this._selectedPlayer = null;
    this._lastRelevantState = null;
    this._initialized = false;
    this._rendered = false;
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error('You must specify an entity (search status sensor)');
    }

    this._config = {
      entity: config.entity,
      result_entities: config.result_entities || [],
      player_entities: config.player_entities || [],
      title: config.title || 'Plex Search',
      show_thumbnails: config.show_thumbnails !== false,
      columns: config.columns || 2,
      theme: config.theme || 'glassmorphic'
    };

    this.render();
  }

  set hass(hass) {
    const nextRelevantState = this._collectRelevantState(hass);
    const shouldUpdate = !this._initialized || this._hasRelevantChange(nextRelevantState);

    this._hass = hass;

    if (!shouldUpdate) {
      return;
    }

    const preservedState = this._captureInputState();

    this.updateResults();
    this.render();
    this._restoreInputState(preservedState);

    this._lastRelevantState = nextRelevantState;
    this._initialized = true;
  }

  getPlayerEntities() {
    // If player_entities specified in config, use those
    if (this._config.player_entities && this._config.player_entities.length > 0) {
      return this._config.player_entities;
    }

    // Otherwise, try to read from sensor's selected_players attribute
    if (this._hass && this._config.entity) {
      const statusEntity = this._hass.states[this._config.entity];
      if (statusEntity && statusEntity.attributes && statusEntity.attributes.selected_players) {
        return statusEntity.attributes.selected_players;
      }
    }

    // Default to empty array
    return [];
  }

  updateResults() {
    if (!this._hass || !this._config.result_entities) return;

    this._results = this._config.result_entities.map(entityId => {
      const state = this._hass.states[entityId];
      if (!state || state.state === 'Empty') return null;

      return {
        entityId,
        title: state.state,
        attributes: state.attributes,
        thumbnail: state.attributes.entity_picture || state.attributes.thumb,
        available: state.attributes.available === true
      };
    }).filter(result => result !== null && result.available);
  }

  _renderPlayerSelector() {
    if (!this.shadowRoot) return;

    const playerSelectorContainer = this.shadowRoot.getElementById('playerSelectorContainer');
    if (!playerSelectorContainer) return;

    const players = this.getPlayerEntities();

    if (players.length > 0) {
      playerSelectorContainer.innerHTML = `
        <div class="player-selector">
          <select class="player-select" id="playerSelect">
            <option value="">Select a player...</option>
            ${players.map(entityId => {
              const state = this._hass?.states[entityId];
              const name = state?.attributes?.friendly_name || entityId;
              const selected = this._selectedPlayer === entityId ? 'selected' : '';
              return `<option value="${entityId}" ${selected}>${name}</option>`;
            }).join('')}
          </select>
        </div>
      `;

      // Re-attach event listener
      const playerSelect = this.shadowRoot.getElementById('playerSelect');
      if (playerSelect) {
        playerSelect.addEventListener('change', (e) => {
          this._selectedPlayer = e.target.value;
        });
      }
    } else {
      playerSelectorContainer.innerHTML = '';
    }
  }

  render() {
    if (!this._config || !this.shadowRoot) return;

    const statusEntity = this._hass?.states[this._config.entity];
    const searchStatus = statusEntity?.state || 'Ready';

    if (!this._rendered) {
      this.shadowRoot.innerHTML = `
        <style>
          :host {
            display: block;
            --card-background: rgba(255, 255, 255, 0.05);
            --card-border: rgba(255, 255, 255, 0.1);
            --text-primary: #ffffff;
            --text-secondary: rgba(255, 255, 255, 0.7);
            --accent-color: #e5a00d;
            --gradient-start: rgba(229, 160, 13, 0.2);
            --gradient-end: rgba(229, 160, 13, 0.05);
          }

          .card-container {
            padding: 20px;
            background: var(--card-background);
            backdrop-filter: blur(10px);
            border-radius: 16px;
            border: 1px solid var(--card-border);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
          }

          .card-header {
            margin-bottom: 20px;
          }

          .card-title {
            font-size: 24px;
            font-weight: 600;
            color: var(--text-primary);
            margin: 0 0 10px 0;
          }

          .search-container {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
          }

          .search-input {
            flex: 1;
            padding: 12px 16px;
            font-size: 16px;
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid var(--card-border);
            border-radius: 8px;
            color: var(--text-primary);
            outline: none;
            transition: all 0.3s ease;
          }

          .search-input:focus {
            background: rgba(255, 255, 255, 0.12);
            border-color: var(--accent-color);
            box-shadow: 0 0 0 3px rgba(229, 160, 13, 0.1);
          }

          .search-button {
            padding: 12px 24px;
            font-size: 16px;
            font-weight: 600;
            background: linear-gradient(135deg, var(--gradient-start), var(--gradient-end));
            border: 1px solid var(--accent-color);
            border-radius: 8px;
            color: var(--text-primary);
            cursor: pointer;
            transition: all 0.3s ease;
          }

          .search-button:hover {
            background: linear-gradient(135deg, rgba(229, 160, 13, 0.3), rgba(229, 160, 13, 0.1));
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(229, 160, 13, 0.3);
          }

          .search-button:active {
            transform: translateY(0);
          }

          .player-selector {
            margin-bottom: 20px;
          }

          .player-select {
            width: 100%;
            padding: 12px 16px;
            font-size: 14px;
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid var(--card-border);
            border-radius: 8px;
            color: var(--text-primary);
            outline: none;
            cursor: pointer;
          }

          .status-bar {
            padding: 10px 16px;
            margin-bottom: 20px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 8px;
            color: var(--text-secondary);
            font-size: 14px;
            text-align: center;
          }

          .results-grid {
            display: grid;
            grid-template-columns: repeat(${this._config.columns}, 1fr);
            gap: 16px;
          }

          @media (max-width: 768px) {
            .results-grid {
              grid-template-columns: 1fr;
            }
          }

          .result-card {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            overflow: hidden;
            transition: all 0.3s ease;
            cursor: pointer;
          }

          .result-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
            border-color: var(--accent-color);
          }

          .result-thumbnail {
            width: 100%;
            aspect-ratio: 2/3;
            object-fit: cover;
            background: linear-gradient(135deg, rgba(255,255,255,0.1), rgba(255,255,255,0.05));
          }

          .result-content {
            padding: 12px;
          }

          .result-title {
            font-size: 16px;
            font-weight: 600;
            color: var(--text-primary);
            margin: 0 0 4px 0;
            overflow: hidden;
            text-overflow: ellipsis;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
          }

          .result-meta {
            font-size: 13px;
            color: var(--text-secondary);
            margin: 0 0 8px 0;
          }

          .result-summary {
            font-size: 12px;
            color: var(--text-secondary);
            margin: 0 0 12px 0;
            overflow: hidden;
            text-overflow: ellipsis;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            line-height: 1.4;
          }

          .play-button {
            width: 100%;
            padding: 8px 16px;
            font-size: 14px;
            font-weight: 600;
            background: linear-gradient(135deg, var(--gradient-start), var(--gradient-end));
            border: 1px solid var(--accent-color);
            border-radius: 6px;
            color: var(--text-primary);
            cursor: pointer;
            transition: all 0.3s ease;
          }

          .play-button:hover {
            background: linear-gradient(135deg, rgba(229, 160, 13, 0.3), rgba(229, 160, 13, 0.1));
          }

          .play-button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
          }

          .no-results {
            text-align: center;
            padding: 40px 20px;
            color: var(--text-secondary);
            font-size: 16px;
          }

          .badge {
            display: inline-block;
            padding: 4px 8px;
            background: rgba(229, 160, 13, 0.2);
            border: 1px solid var(--accent-color);
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            color: var(--accent-color);
            text-transform: uppercase;
            margin-right: 6px;
          }
        </style>

        <div class="card-container">
          <div class="card-header">
            <h2 class="card-title">${this._config.title}</h2>
          </div>

          <div class="search-container">
            <input
              type="text"
              class="search-input"
              placeholder="Search for movies, TV shows..."
              id="searchInput"
            />
            <button class="search-button" id="searchButton" type="button">
              Search
            </button>
          </div>

          <div id="playerSelectorContainer"></div>

          <div class="status-bar" id="statusBar">
            ${searchStatus}
          </div>

          <div id="resultsWrapper"></div>
        </div>
      `;

      this.attachEventListeners();
      this._rendered = true;
    }

    this._renderPlayerSelector();
    this._updateStatus(searchStatus);
    this._renderResults();
  }

  renderResultCard(result) {
    const { attributes, thumbnail, title } = result;
    const mediaType = attributes.media_type || 'unknown';
    const year = attributes.year || '';
    const rating = attributes.rating ? `★ ${attributes.rating.toFixed(1)}` : '';
    const summary = attributes.summary || '';
    const ratingKey = attributes.rating_key;

    return `
      <div class="result-card" data-rating-key="${ratingKey}">
        ${this._config.show_thumbnails && thumbnail ? `
          <img class="result-thumbnail" src="${thumbnail}" alt="${title}" />
        ` : ''}
        <div class="result-content">
          <h3 class="result-title">${title}</h3>
          <div class="result-meta">
            <span class="badge">${mediaType}</span>
            ${year ? `<span>${year}</span>` : ''}
            ${rating ? `<span>${rating}</span>` : ''}
          </div>
          ${summary ? `<p class="result-summary">${summary}</p>` : ''}
          <button class="play-button" data-rating-key="${ratingKey}">
            ▶ Play
          </button>
        </div>
      </div>
    `;
  }

  attachEventListeners() {
    const searchButton = this.shadowRoot.getElementById('searchButton');
    const searchInput = this.shadowRoot.getElementById('searchInput');
    const playerSelect = this.shadowRoot.getElementById('playerSelect');

    if (searchButton && searchInput) {
      searchButton.addEventListener('click', () => this.performSearch());
      searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') this.performSearch();
      });
    }

    if (playerSelect) {
      playerSelect.addEventListener('change', (e) => {
        this._selectedPlayer = e.target.value;
      });
    }

    // Play buttons
    const playButtons = this.shadowRoot.querySelectorAll('.play-button');
    playButtons.forEach(button => {
      button.addEventListener('click', (e) => {
        e.stopPropagation();
        const ratingKey = button.getAttribute('data-rating-key');
        this.playMedia(ratingKey);
      });
    });
  }

  _captureInputState() {
    if (!this.shadowRoot) return null;

    const searchInput = this.shadowRoot.getElementById('searchInput');
    const playerSelect = this.shadowRoot.getElementById('playerSelect');
    const activeId = this.shadowRoot.activeElement?.id;

    return {
      query: searchInput?.value || '',
      player: playerSelect?.value || '',
      focusedId: activeId === 'searchInput' || activeId === 'playerSelect' ? activeId : null,
      selectionStart: searchInput?.selectionStart,
      selectionEnd: searchInput?.selectionEnd
    };
  }

  _restoreInputState(state) {
    if (!state || !this.shadowRoot) return;

    const searchInput = this.shadowRoot.getElementById('searchInput');
    const playerSelect = this.shadowRoot.getElementById('playerSelect');

    if (searchInput) {
      searchInput.value = state.query || '';
    }

    if (playerSelect) {
      playerSelect.value = state.player || '';
      this._selectedPlayer = playerSelect.value || null;
    }

    if (state.focusedId === 'searchInput' && searchInput) {
      searchInput.focus();
      if (
        typeof state.selectionStart === 'number' &&
        typeof state.selectionEnd === 'number'
      ) {
        searchInput.setSelectionRange(state.selectionStart, state.selectionEnd);
      }
    } else if (state.focusedId === 'playerSelect' && playerSelect) {
      playerSelect.focus();
    }
  }

  _updateStatus(statusText) {
    const statusBar = this.shadowRoot?.getElementById('statusBar');
    if (statusBar) {
      statusBar.textContent = statusText;
    }
  }

  _renderResults() {
    if (!this.shadowRoot) return;

    const wrapper = this.shadowRoot.getElementById('resultsWrapper');
    if (!wrapper) return;

    if (!this._results.length) {
      wrapper.innerHTML = `
        <div class="no-results">
          Enter a search query to find Plex media
        </div>
      `;
      return;
    }

    wrapper.innerHTML = `
      <div class="results-grid">
        ${this._results.map(result => this.renderResultCard(result)).join('')}
      </div>
    `;

    // Re-attach play button listeners after re-rendering results list
    const playButtons = this.shadowRoot.querySelectorAll('.play-button');
    playButtons.forEach(button => {
      button.addEventListener('click', (e) => {
        e.stopPropagation();
        const ratingKey = button.getAttribute('data-rating-key');
        this.playMedia(ratingKey);
      });
    });
  }

  _collectRelevantState(hass) {
    const snapshot = {};

    if (this._config?.entity) {
      const stateObj = hass.states[this._config.entity];
      snapshot[this._config.entity] = stateObj
        ? `${stateObj.state}-${JSON.stringify(stateObj.attributes)}`
        : null;
    }

    if (Array.isArray(this._config?.result_entities)) {
      this._config.result_entities.forEach((id) => {
        const stateObj = hass.states[id];
        snapshot[id] = stateObj
          ? `${stateObj.state}-${JSON.stringify(stateObj.attributes)}`
          : null;
      });
    }

    return snapshot;
  }

  _hasRelevantChange(nextState) {
    if (!this._lastRelevantState) return true;

    const prevKeys = Object.keys(this._lastRelevantState);
    const nextKeys = Object.keys(nextState);
    if (prevKeys.length !== nextKeys.length) return true;

    for (const key of nextKeys) {
      if (this._lastRelevantState[key] !== nextState[key]) {
        return true;
      }
    }

    return false;
  }

  performSearch() {
    const searchInput = this.shadowRoot.getElementById('searchInput');
    const query = searchInput?.value?.trim();

    if (!query) {
      alert('Please enter a search query');
      return;
    }

    this._hass.callService('plex_search_play', 'search', {
      query: query,
      limit: 6
    });
  }

  playMedia(ratingKey) {
    if (!this._selectedPlayer) {
      alert('Please select a media player first');
      return;
    }

    this._hass.callService('plex_search_play', 'play_media', {
      rating_key: ratingKey,
      player_entity_id: this._selectedPlayer
    });
  }

  getCardSize() {
    return 3;
  }
}

customElements.define('plex-search-card', PlexSearchCard);

// Register the card with Home Assistant
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'plex-search-card',
  name: 'Plex Search Card',
  description: 'A card for searching and playing Plex media',
  preview: false,
  documentationURL: 'https://github.com/InfoSecured/plex-search-and-play'
});

console.info(
  '%c PLEX-SEARCH-CARD %c v1.0.0 ',
  'color: white; background: #e5a00d; font-weight: 700;',
  'color: #e5a00d; background: white; font-weight: 700;'
);
