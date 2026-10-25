class EvControlCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    if (!config.entity) {
      throw new Error('entity is required');
    }
    this.historyData = [];
    this.chart = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: 'open' });
      this.render();
      this.loadHistoryData();
    }
    this.updateData();
  }

  loadHistoryData() {
    // Carica dati storici della batteria dell'auto dalle ultime 24 ore
    const now = new Date();
    const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    
    // Legge il nome dell'entità dal config passato dal wizard
    let evSocEntity = this.config.ev_soc_entity;
    
    if (!evSocEntity) {
      console.warn('ev_soc_entity not configured');
      this.initChart();
      return;
    }
    
    console.log('Loading history for entity:', evSocEntity);
    
    this._hass.callApi('GET', `history/period/${yesterday.toISOString()}?filter_entity_ids=${evSocEntity}&minimal_response=false`)
      .then(data => {
        console.log('History data received:', data);
        if (data && data[0] && data[0].length > 0) {
          this.historyData = data[0].map(item => ({
            time: new Date(item.last_updated),
            soc: item.state ? parseFloat(item.state) : 0
          }));
          console.log('Processed history points:', this.historyData.length);
          this.initChart();
        } else {
          console.warn(`No history data found for ${evSocEntity}`);
          this.initChart();
        }
      })
      .catch(err => {
        console.error(`Error loading history for ${evSocEntity}:`, err);
        this.initChart();
      });
  }

  initChart() {
    const canvas = this.shadowRoot?.querySelector('#charging-chart');
    if (!canvas || typeof Chart === 'undefined') return;

    const ctx = canvas.getContext('2d');
    
    // Se non ci sono dati storici, mostra solo il valore attuale
    let labels = [];
    let data = [];
    
    if (this.historyData && this.historyData.length > 0) {
      labels = this.historyData.map(d => d.time.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' }));
      data = this.historyData.map(d => d.soc);
    } else {
      // Fallback: genera una linea con il valore attuale
      const currentSoc = this._hass?.states?.[this.config.entity]?.attributes?.ev_soc || 0;
      labels = ['--:--'];
      data = [currentSoc];
    }

    // Distruggi il grafico precedente se esiste
    if (this.chart) {
      this.chart.destroy();
    }

    this.chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: 'EV Battery SOC (%)',
          data: data,
          borderColor: '#64b5f6',
          backgroundColor: 'rgba(100, 181, 246, 0.1)',
          borderWidth: 2,
          fill: true,
          tension: 0.4,
          pointRadius: 2,
          pointHoverRadius: 5,
          pointBackgroundColor: '#64b5f6',
          pointBorderColor: '#fff',
          pointBorderWidth: 1,
          min: 0,
          max: 100
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: {
          legend: {
            display: true,
            labels: { color: '#90caf9', font: { size: 11 } }
          }
        },
        scales: {
          y: {
            min: 0,
            max: 100,
            grid: { color: 'rgba(100, 181, 246, 0.1)' },
            ticks: { color: '#90caf9', callback: function(value) { return value + '%'; } }
          },
          x: {
            grid: { display: false },
            ticks: { color: '#90caf9', maxTicksLimit: 6 }
          }
        }
      }
    });
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        .ev-card {
          background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
          border-radius: 20px;
          padding: 28px;
          color: #fff;
          font-family: 'Inter', 'Segoe UI', sans-serif;
          box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.1);
          backdrop-filter: blur(10px);
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 24px;
        }
        
        .ev-column {
          display: flex;
          flex-direction: column;
          gap: 20px;
        }
        
        .ev-column.full {
          grid-column: 1 / -1;
        }
        
        .ev-header {
          text-align: center;
          margin-bottom: 28px;
          position: relative;
        }
        
        .ev-title {
          font-size: 28px;
          font-weight: 700;
          margin-bottom: 8px;
          background: linear-gradient(135deg, #64b5f6 0%, #42a5f5 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          background-clip: text;
          letter-spacing: -0.5px;
        }
        
        .ev-subtitle {
          font-size: 13px;
          color: #90caf9;
          font-weight: 500;
          letter-spacing: 0.5px;
          text-transform: uppercase;
          animation: pulse 2s ease-in-out infinite;
        }
        
        @keyframes pulse {
          0%, 100% { opacity: 0.6; }
          50% { opacity: 1; }
        }
        
        .ev-viz-container {
          margin: 32px 0;
          padding: 24px;
          background: rgba(255, 255, 255, 0.03);
          border-radius: 16px;
          border: 1px solid rgba(100, 181, 246, 0.2);
          box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.2);
        }
        
        .ev-car-svg {
          width: 100%;
          max-width: 280px;
          margin: 0 auto;
          display: block;
          filter: drop-shadow(0 4px 12px rgba(100, 181, 246, 0.3));
        }
        
        .ev-battery-bar {
          width: 100%;
          height: 24px;
          background: rgba(0, 0, 0, 0.3);
          border-radius: 12px;
          overflow: hidden;
          margin-top: 16px;
          border: 1px solid rgba(100, 181, 246, 0.3);
          position: relative;
        }
        
        .ev-battery-fill {
          height: 100%;
          background: linear-gradient(90deg, #4CAF50 0%, #8BC34A 100%);
          transition: width 0.6s ease;
          position: relative;
          box-shadow: inset -2px 0 4px rgba(255, 255, 255, 0.2);
        }
        
        .ev-battery-text {
          position: absolute;
          top: 50%;
          left: 50%;
          transform: translate(-50%, -50%);
          font-size: 12px;
          font-weight: 600;
          color: #fff;
          z-index: 10;
          text-shadow: 0 1px 2px rgba(0, 0, 0, 0.5);
        }
        
        .ev-stats-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
          gap: 12px;
          margin-bottom: 24px;
        }
        
        .ev-stat-box {
          background: linear-gradient(135deg, rgba(100, 181, 246, 0.1) 0%, rgba(66, 165, 245, 0.05) 100%);
          border: 1px solid rgba(100, 181, 246, 0.2);
          border-radius: 12px;
          padding: 14px 10px;
          text-align: center;
          transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
          backdrop-filter: blur(10px);
          cursor: default;
          min-height: 100px;
          display: flex;
          flex-direction: column;
          justify-content: center;
          align-items: center;
        }
        
        .ev-stat-box:hover {
          background: linear-gradient(135deg, rgba(100, 181, 246, 0.15) 0%, rgba(66, 165, 245, 0.08) 100%);
          border-color: rgba(100, 181, 246, 0.4);
          transform: translateY(-2px);
          box-shadow: 0 8px 16px rgba(100, 181, 246, 0.1);
        }
        
        .ev-stat-icon {
          font-size: 24px;
          margin-bottom: 8px;
          display: block;
        }
        
        .ev-stat-label {
          font-size: 11px;
          color: #90caf9;
          text-transform: uppercase;
          font-weight: 600;
          letter-spacing: 0.4px;
          margin-bottom: 6px;
          display: block;
          white-space: nowrap;
        }
        
        .ev-stat-value {
          font-size: 20px;
          font-weight: 700;
          color: #64b5f6;
          font-family: 'Monaco', 'Courier New', monospace;
          word-break: break-word;
        }
        
        .ev-controls {
          margin-top: 0;
          border-top: none;
          padding-top: 0;
        }
        
        .ev-control-section {
          margin-bottom: 14px;
        }
        
        .ev-control-label {
          font-size: 12px;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          color: #90caf9;
          font-weight: 600;
          margin-bottom: 8px;
          display: block;
        }
        
        .ev-select, .ev-input {
          width: 100%;
          padding: 10px 12px;
          border-radius: 8px;
          background: linear-gradient(135deg, rgba(0, 0, 0, 0.3) 0%, rgba(0, 0, 0, 0.1) 100%);
          color: #fff;
          border: 1px solid rgba(100, 181, 246, 0.4);
          font-size: 12px;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.3s;
          font-family: inherit;
        }
        
        .ev-input {
          cursor: text;
        }
        
        .ev-select:hover, .ev-input:hover {
          border-color: rgba(100, 181, 246, 0.7);
          box-shadow: 0 0 12px rgba(100, 181, 246, 0.2);
        }
        
        .ev-select:focus, .ev-input:focus {
          outline: none;
          border-color: #64b5f6;
          box-shadow: 0 0 16px rgba(100, 181, 246, 0.35);
        }

        .ev-slider-container {
          display: flex;
          gap: 12px;
          align-items: center;
        }

        .ev-slider {
          flex: 1;
          height: 6px;
          border-radius: 3px;
          background: linear-gradient(135deg, rgba(0, 0, 0, 0.3) 0%, rgba(0, 0, 0, 0.1) 100%);
          outline: none;
          -webkit-appearance: none;
          appearance: none;
          cursor: pointer;
        }

        .ev-slider::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          width: 18px;
          height: 18px;
          border-radius: 50%;
          background: linear-gradient(135deg, #64b5f6 0%, #42a5f5 100%);
          cursor: pointer;
          box-shadow: 0 2px 8px rgba(100, 181, 246, 0.4);
        }

        .ev-slider::-moz-range-thumb {
          width: 18px;
          height: 18px;
          border-radius: 50%;
          background: linear-gradient(135deg, #64b5f6 0%, #42a5f5 100%);
          cursor: pointer;
          border: none;
          box-shadow: 0 2px 8px rgba(100, 181, 246, 0.4);
        }

        .ev-slider-value {
          min-width: 50px;
          text-align: center;
          color: #64b5f6;
          font-weight: 600;
          font-size: 12px;
        }
        
        .ev-button-group {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 10px;
        }
        
        .ev-button {
          padding: 10px 12px;
          border-radius: 8px;
          border: 1px solid rgba(100, 181, 246, 0.5);
          background: linear-gradient(135deg, rgba(100, 181, 246, 0.25) 0%, rgba(66, 165, 245, 0.1) 100%);
          color: #64b5f6;
          font-size: 12px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
          text-transform: uppercase;
          letter-spacing: 0.5px;
          font-family: inherit;
          text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
        }
        
        .ev-button:hover {
          background: linear-gradient(135deg, rgba(100, 181, 246, 0.35) 0%, rgba(66, 165, 245, 0.2) 100%);
          border-color: rgba(100, 181, 246, 0.8);
          box-shadow: 0 8px 16px rgba(100, 181, 246, 0.25);
          transform: translateY(-2px);
        }
        
        .ev-button:active {
          transform: translateY(0);
        }

        .ev-chart-container {
          background: rgba(0, 0, 0, 0.3);
          border: 1px solid rgba(100, 181, 246, 0.2);
          border-radius: 12px;
          padding: 14px;
          flex: 1;
        }

        .ev-chart-title {
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          color: #90caf9;
          font-weight: 600;
          margin-bottom: 12px;
          display: block;
        }
        
        .ev-debug-section {
          margin-top: 28px;
          padding-top: 24px;
          border-top: 1px solid rgba(100, 181, 246, 0.2);
        }
        
        .ev-debug-title {
          font-size: 12px;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          color: #90caf9;
          font-weight: 600;
          margin-bottom: 16px;
          display: block;
        }
        
        .ev-debug-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
          gap: 12px;
        }
        
        .ev-debug-item {
          background: rgba(0, 0, 0, 0.4);
          border: 1px solid rgba(100, 181, 246, 0.2);
          border-radius: 8px;
          padding: 10px;
          text-align: center;
          font-size: 11px;
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        
        .ev-debug-label {
          color: #90caf9;
          font-weight: 600;
          letter-spacing: 0.3px;
          text-transform: uppercase;
          font-size: 10px;
        }
        
        .ev-debug-value {
          color: #64b5f6;
          font-family: 'Monaco', 'Courier New', monospace;
          font-weight: 700;
          font-size: 12px;
        }

        @media (max-width: 768px) {
          .ev-card {
            padding: 16px;
            grid-template-columns: 1fr;
            gap: 16px;
          }

          .ev-header {
            margin-bottom: 16px;
          }

          .ev-title {
            font-size: 22px;
          }

          .ev-subtitle {
            font-size: 11px;
          }

          .ev-viz-container {
            margin: 16px 0;
            padding: 16px;
          }

          .ev-car-svg {
            max-width: 200px;
          }

          .ev-stats-grid {
            grid-template-columns: repeat(3, 1fr);
            gap: 8px;
            margin-bottom: 16px;
          }

          .ev-stat-box {
            padding: 10px 8px;
            min-height: 80px;
          }

          .ev-stat-icon {
            font-size: 18px;
            margin-bottom: 4px;
          }

          .ev-stat-label {
            font-size: 10px;
            margin-bottom: 4px;
          }

          .ev-stat-value {
            font-size: 16px;
          }

          .ev-controls {
            margin-top: 0;
          }

          .ev-control-section {
            margin-bottom: 10px;
          }

          .ev-control-label {
            font-size: 11px;
            margin-bottom: 6px;
          }

          .ev-select, .ev-input {
            padding: 8px 10px;
            font-size: 11px;
          }

          .ev-slider-container {
            gap: 8px;
          }

          .ev-slider-value {
            min-width: 40px;
            font-size: 11px;
          }

          .ev-button-group {
            gap: 8px;
          }

          .ev-button {
            padding: 8px 10px;
            font-size: 11px;
          }

          .ev-chart-container {
            padding: 10px;
          }

          .ev-chart-title {
            font-size: 10px;
            margin-bottom: 8px;
          }

          .ev-debug-grid {
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
          }

          .ev-debug-item {
            padding: 8px;
            font-size: 10px;
          }

          .ev-debug-label {
            font-size: 9px;
          }

          .ev-debug-value {
            font-size: 11px;
          }
        }
      </style>

      <div class="ev-card">
        <!-- COLONNA SINISTRA: Header, Visualizzazione Auto, Batteria -->
        <div class="ev-column">
          <div class="ev-header">
            <div class="ev-title">⚡ EV CONTROL</div>
            <div class="ev-subtitle" id="mode-display">
              ECO • 0A Disponibili
            </div>
          </div>

          <div class="ev-viz-container">
            <svg class="ev-car-svg" viewBox="0 0 300 140" xmlns="http://www.w3.org/2000/svg">
              <defs>
                <linearGradient id="carGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" style="stop-color:#64b5f6;stop-opacity:0.8" />
                  <stop offset="100%" style="stop-color:#42a5f5;stop-opacity:0.6" />
                </linearGradient>
                <filter id="carShadow">
                  <feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.3"/>
                </filter>
              </defs>
              <g filter="url(#carShadow)">
                <rect x="60" y="70" width="180" height="45" rx="8" fill="url(#carGrad)" stroke="#90caf9" stroke-width="2"/>
                <rect x="80" y="45" width="140" height="30" rx="6" fill="url(#carGrad)" stroke="#90caf9" stroke-width="2"/>
                <circle cx="85" cy="120" r="10" fill="#42a5f5" stroke="#90caf9" stroke-width="1.5"/>
                <circle cx="215" cy="120" r="10" fill="#42a5f5" stroke="#90caf9" stroke-width="1.5"/>
                <rect x="95" y="50" width="35" height="25" rx="3" fill="rgba(100, 181, 246, 0.3)" stroke="#64b5f6" stroke-width="1"/>
                <rect x="170" y="50" width="35" height="25" rx="3" fill="rgba(100, 181, 246, 0.3)" stroke="#64b5f6" stroke-width="1"/>
              </g>
            </svg>
            
            <div class="ev-battery-bar">
              <div class="ev-battery-fill" id="battery-fill" style="width: 0%; background: linear-gradient(90deg, #4CAF50 0%, #8BC34A 100%);"></div>
              <div class="ev-battery-text" id="battery-text">0%</div>
            </div>
          </div>

          <div class="ev-stats-grid">
            <div class="ev-stat-box">
              <div class="ev-stat-icon">🔌</div>
              <div class="ev-stat-label">Corrente</div>
              <div class="ev-stat-value" id="stat-current">0A</div>
            </div>
            <div class="ev-stat-box">
              <div class="ev-stat-icon">☀️</div>
              <div class="ev-stat-label">PV Power</div>
              <div class="ev-stat-value" id="stat-pv">0kW</div>
            </div>
            <div class="ev-stat-box">
              <div class="ev-stat-icon">⚡</div>
              <div class="ev-stat-label">Surplus</div>
              <div class="ev-stat-value" id="stat-surplus">0kW</div>
            </div>
            <div class="ev-stat-box">
              <div class="ev-stat-icon">🏠</div>
              <div class="ev-stat-label">Consumo</div>
              <div class="ev-stat-value" id="stat-consumption">0kW</div>
            </div>
            <div class="ev-stat-box">
              <div class="ev-stat-icon">🔋</div>
              <div class="ev-stat-label">Batteria</div>
              <div class="ev-stat-value" id="stat-battery">0%</div>
            </div>
            <div class="ev-stat-box">
              <div class="ev-stat-icon">⚙️</div>
              <div class="ev-stat-label">Tensione</div>
              <div class="ev-stat-value" id="stat-voltage">230V</div>
            </div>
          </div>
        </div>

        <!-- COLONNA DESTRA: Controlli e Grafico -->
        <div class="ev-column">
          <div class="ev-controls">
            <div class="ev-control-section">
              <label class="ev-control-label">📋 Modalità di Carica</label>
              <select class="ev-select" id="mode-select">
                <option value="eco">🌱 ECO (Solar)</option>
                <option value="normal">⚙️ NORMAL (Balanced)</option>
                <option value="power">⚡ POWER (Max)</option>
                <option value="auto">🕐 AUTO (Schedule)</option>
                <option value="auto-eco">🌱⚙️ AUTO-ECO (Hybrid)</option>
              </select>
            </div>

            <div class="ev-control-section">
              <label class="ev-control-label">⚡ Velocità Carica (NORMAL)</label>
              <div class="ev-slider-container">
                <input type="range" id="speed-slider" class="ev-slider" min="6" max="32" value="16" step="1">
                <div class="ev-slider-value"><span id="speed-value">16</span>A</div>
              </div>
            </div>

            <div class="ev-control-section">
              <label class="ev-control-label">⏰ Orario Inizio (AUTO)</label>
              <input type="time" id="start-time" class="ev-input">
            </div>

            <div class="ev-control-section">
              <label class="ev-control-label">⏰ Orario Fine (AUTO)</label>
              <input type="time" id="end-time" class="ev-input">
            </div>

            <div class="ev-control-section">
              <div class="ev-button-group">
                <button class="ev-button" id="btn-start">▶️ Start</button>
                <button class="ev-button" id="btn-stop">⏹️ Stop</button>
              </div>
            </div>
          </div>

          <div class="ev-chart-container">
            <div class="ev-chart-title">📈 Carica Ultime 24h</div>
            <canvas id="charging-chart" style="max-height: 200px;"></canvas>
          </div>
        </div>

        <!-- DEBUG PANEL - Full Width -->
        <div class="ev-column full">
          <div class="ev-debug-section">
            <div class="ev-debug-title">🔧 Debug Info</div>
            <div class="ev-debug-grid">
              <div class="ev-debug-item">
                <span class="ev-debug-label">Status</span>
                <span class="ev-debug-value" id="debug-status">Running</span>
              </div>
              <div class="ev-debug-item">
                <span class="ev-debug-label">PV</span>
                <span class="ev-debug-value" id="debug-pv">0kW</span>
              </div>
              <div class="ev-debug-item">
                <span class="ev-debug-label">Battery</span>
                <span class="ev-debug-value" id="debug-battery">0kW</span>
              </div>
              <div class="ev-debug-item">
                <span class="ev-debug-label">EV</span>
                <span class="ev-debug-value" id="debug-ev">0kW</span>
              </div>
              <div class="ev-debug-item">
                <span class="ev-debug-label">Grid</span>
                <span class="ev-debug-value" id="debug-grid">0kW</span>
              </div>
              <div class="ev-debug-item">
                <span class="ev-debug-label">EV SOC</span>
                <span class="ev-debug-value" id="debug-ev-soc">0%</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;

    // Setup event listeners
    this.shadowRoot.getElementById('mode-select').addEventListener('change', (e) => this.handleModeChange(e));
    this.shadowRoot.getElementById('btn-start').addEventListener('click', () => this.handleStartCharging());
    this.shadowRoot.getElementById('btn-stop').addEventListener('click', () => this.handleStopCharging());
    
    // Speed slider
    const speedSlider = this.shadowRoot.getElementById('speed-slider');
    const speedValue = this.shadowRoot.getElementById('speed-value');
    speedSlider.addEventListener('input', (e) => {
      speedValue.textContent = e.target.value;
      this.handleSpeedChange(e.target.value);
    });

    // Time inputs
    this.shadowRoot.getElementById('start-time').addEventListener('change', (e) => this.handleStartTimeChange(e));
    this.shadowRoot.getElementById('end-time').addEventListener('change', (e) => this.handleEndTimeChange(e));
  }

  updateData() {
    const state = this._hass.states[this.config.entity];
    if (!state) return;

    const attrs = state.attributes;
    
    // Update mode and current display
    const mode = (attrs.mode || 'eco').toUpperCase();
    const current = attrs.available_current || 0;
    this.shadowRoot.getElementById('mode-display').textContent = `${mode} • ${current}A Disponibili`;
    
    // Update selector to match current mode
    const modeSelect = this.shadowRoot.getElementById('mode-select');
    modeSelect.value = (attrs.mode || 'eco').toLowerCase();

    // Update battery display
    const evSoc = attrs.ev_soc || 0;
    const batFill = this.shadowRoot.getElementById('battery-fill');
    const batText = this.shadowRoot.getElementById('battery-text');
    
    batText.textContent = `${evSoc}%`;
    batFill.style.width = `${evSoc}%`;
    
    // Determine battery color based on SOC
    if (evSoc >= 80) {
      batFill.style.background = 'linear-gradient(90deg, #4CAF50 0%, #8BC34A 100%)';
    } else if (evSoc >= 50) {
      batFill.style.background = 'linear-gradient(90deg, #FFC107 0%, #FFA000 100%)';
    } else {
      batFill.style.background = 'linear-gradient(90deg, #F44336 0%, #E53935 100%)';
    }

    // Update stats
    this.shadowRoot.getElementById('stat-current').textContent = `${current}A`;
    this.shadowRoot.getElementById('stat-pv').textContent = `${((attrs.pv_power || 0) / 1000).toFixed(1)}kW`;
    this.shadowRoot.getElementById('stat-surplus').textContent = `${((attrs.surplus || 0) / 1000).toFixed(1)}kW`;
    this.shadowRoot.getElementById('stat-consumption').textContent = `${((attrs.house_consumption || 0) / 1000).toFixed(1)}kW`;
    this.shadowRoot.getElementById('stat-battery').textContent = `${attrs.battery_soc || 0}%`;
    this.shadowRoot.getElementById('stat-voltage').textContent = `${attrs.voltage || 230}V`;

    // Update debug info
    this.shadowRoot.getElementById('debug-status').textContent = attrs.debug_message || 'Running';
    this.shadowRoot.getElementById('debug-pv').textContent = `${((attrs.pv_power || 0) / 1000).toFixed(2)}kW`;
    this.shadowRoot.getElementById('debug-battery').textContent = `${((attrs.battery_power || 0) / 1000).toFixed(2)}kW`;
    this.shadowRoot.getElementById('debug-ev').textContent = `${((attrs.ev_power || 0) / 1000).toFixed(2)}kW`;
    this.shadowRoot.getElementById('debug-grid').textContent = `${((attrs.grid_power_limit || 0) * 1000).toFixed(2)}kW`;
    this.shadowRoot.getElementById('debug-ev-soc').textContent = `${evSoc}%`;
  }

  handleModeChange(event) {
    const newMode = event.target.value.toUpperCase();
    this._hass.callService('evcontrol', 'set_mode', {
      mode: newMode,
      entity_id: this.config.entity
    });
  }

  handleStartCharging() {
    this._hass.callService('evcontrol', 'start_charging', {
      entity_id: this.config.entity
    });
  }

  handleStopCharging() {
    this._hass.callService('evcontrol', 'stop_charging', {
      entity_id: this.config.entity
    });
  }

  handleSpeedChange(speed) {
    this._hass.callService('evcontrol', 'set_speed', {
      speed: parseInt(speed),
      entity_id: this.config.entity
    });
  }

  handleStartTimeChange(event) {
    this._hass.callService('evcontrol', 'set_start_time', {
      start_time: event.target.value,
      entity_id: this.config.entity
    });
  }

  handleEndTimeChange(event) {
    this._hass.callService('evcontrol', 'set_end_time', {
      end_time: event.target.value,
      entity_id: this.config.entity
    });
  }

  static getConfigElement() {
    return document.createElement('evcontrol-card-editor');
  }

  static getStubConfig() {
    return {
      entity: 'sensor.debug_info',
      type: 'custom:evcontrol-card'
    };
  }
}

customElements.define('evcontrol-card', EvControlCard);

// Simple editor for the card
class EvControlCardEditor extends HTMLElement {
  setConfig(config) {
    this.config = config;
  }

  connectedCallback() {
    this.innerHTML = `
      <div style="padding: 16px;">
        <ha-entity-picker label="Entity" entity-type="sensor"></ha-entity-picker>
      </div>
    `;
  }
}

customElements.define('evcontrol-card-editor', EvControlCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'evcontrol-card',
  name: 'EV Control Card',
  description: 'Professional EV charging control dashboard',
  preview: true,
  documentationURL: 'https://github.com/gian1992/evcontrol'
});

// Load Chart.js library
if (!window.Chart) {
  const script = document.createElement('script');
  script.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js';
  script.async = true;
  document.head.appendChild(script);
}
