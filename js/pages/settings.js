/**
 * SUPERRICH - Settings Page
 */
function renderSettings(container) {
  container.innerHTML = `
    <div class="settings-layout">
      <!-- Settings Nav -->
      <nav class="settings-nav">
        <div class="settings-nav-item active" data-section="connection">
          ${Utils.icon('wifi')} MT5 Connection
        </div>
        <div class="settings-nav-item" data-section="risk">
          ${Utils.icon('shield')} Risk Management
        </div>
        <div class="settings-nav-item" data-section="notifications">
          ${Utils.icon('bell')} Notifications
        </div>
        <div class="settings-nav-item" data-section="display">
          ${Utils.icon('moon')} Display
        </div>
        <div class="settings-nav-item" data-section="api">
          ${Utils.icon('key')} API Keys
        </div>
      </nav>

      <!-- Settings Content -->
      <div class="settings-content">
        <!-- MT5 Connection -->
        <section class="settings-section" id="section-connection">
          <h3 class="settings-section-title">MT5 Connection</h3>
          <p class="settings-section-desc">Configure your MetaTrader 5 terminal connection settings.</p>
          <div class="settings-group">
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Server</div>
                <div class="settings-row-desc">MT5 broker server address</div>
              </div>
              <div class="settings-row-action">
                <input class="input" type="text" value="MetaQuotes-Demo" style="width:200px">
              </div>
            </div>
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Login</div>
                <div class="settings-row-desc">Your MT5 account number</div>
              </div>
              <div class="settings-row-action">
                <input class="input input-mono" type="text" value="12345678" style="width:200px">
              </div>
            </div>
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Password</div>
                <div class="settings-row-desc">Account password (stored locally)</div>
              </div>
              <div class="settings-row-action">
                <input class="input" type="password" value="••••••••" style="width:200px">
              </div>
            </div>
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Auto Reconnect</div>
                <div class="settings-row-desc">Automatically reconnect on disconnection</div>
              </div>
              <div class="settings-row-action">
                <label class="toggle"><input type="checkbox" checked><span class="toggle-slider"></span></label>
              </div>
            </div>
          </div>
          <div style="display:flex;gap:var(--space-3);margin-top:var(--space-4)">
            <button class="btn btn-primary">Test Connection</button>
            <button class="btn btn-secondary">Save Changes</button>
          </div>
          <div class="connection-test success" style="margin-top:var(--space-4)">
            <span style="color:var(--profit);font-size:20px">✓</span>
            <div>
              <div style="font-weight:600;color:var(--profit)">Connected Successfully</div>
              <div class="text-sm text-muted">Latency: 12ms · Server: MetaQuotes-Demo</div>
            </div>
          </div>
        </section>

        <!-- Risk Management -->
        <section class="settings-section" id="section-risk">
          <h3 class="settings-section-title">Risk Management</h3>
          <p class="settings-section-desc">Set trading limits to protect your account.</p>
          <div class="settings-group">
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Max Open Positions</div>
                <div class="settings-row-desc">Maximum number of simultaneous positions</div>
              </div>
              <div class="settings-row-action">
                <input class="input input-mono" type="number" value="10" min="1" max="50" style="width:100px">
              </div>
            </div>
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Daily Loss Limit</div>
                <div class="settings-row-desc">Stop trading when daily loss exceeds this amount</div>
              </div>
              <div class="settings-row-action">
                <input class="input input-mono" type="number" value="500" min="0" step="50" style="width:120px">
              </div>
            </div>
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Max Lot Size</div>
                <div class="settings-row-desc">Maximum volume per single order</div>
              </div>
              <div class="settings-row-action">
                <input class="input input-mono" type="number" value="5.00" min="0.01" step="0.1" style="width:120px">
              </div>
            </div>
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Max Drawdown Alert</div>
                <div class="settings-row-desc">Alert when drawdown exceeds percentage</div>
              </div>
              <div class="settings-row-action">
                <div style="display:flex;align-items:center;gap:var(--space-2)">
                  <input class="input input-mono" type="number" value="10" min="1" max="100" style="width:80px">
                  <span class="text-sm text-muted">%</span>
                </div>
              </div>
            </div>
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Emergency Stop</div>
                <div class="settings-row-desc">Close all positions and stop all strategies</div>
              </div>
              <div class="settings-row-action">
                <button class="btn btn-danger btn-sm">${Utils.icon('stop-circle')} Kill Switch</button>
              </div>
            </div>
          </div>
        </section>

        <!-- Notifications -->
        <section class="settings-section" id="section-notifications">
          <h3 class="settings-section-title">Notifications</h3>
          <p class="settings-section-desc">Configure how you receive alerts.</p>
          <div class="settings-group">
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Browser Notifications</div>
                <div class="settings-row-desc">Show desktop notifications for alerts</div>
              </div>
              <div class="settings-row-action">
                <label class="toggle"><input type="checkbox" checked><span class="toggle-slider"></span></label>
              </div>
            </div>
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Sound Alerts</div>
                <div class="settings-row-desc">Play sound for trade executions</div>
              </div>
              <div class="settings-row-action">
                <label class="toggle"><input type="checkbox" checked><span class="toggle-slider"></span></label>
              </div>
            </div>
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Trade Notifications</div>
                <div class="settings-row-desc">Notify when orders are filled</div>
              </div>
              <div class="settings-row-action">
                <label class="toggle"><input type="checkbox" checked><span class="toggle-slider"></span></label>
              </div>
            </div>
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Strategy Alerts</div>
                <div class="settings-row-desc">Notify on strategy errors or stops</div>
              </div>
              <div class="settings-row-action">
                <label class="toggle"><input type="checkbox" checked><span class="toggle-slider"></span></label>
              </div>
            </div>
          </div>
        </section>

        <!-- Display -->
        <section class="settings-section" id="section-display">
          <h3 class="settings-section-title">Display</h3>
          <p class="settings-section-desc">Customize the appearance.</p>
          <div class="settings-group">
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Theme</div>
                <div class="settings-row-desc">Choose between dark and light mode</div>
              </div>
              <div class="settings-row-action">
                <select class="select">
                  <option>Dark Mode</option>
                  <option>Light Mode</option>
                  <option>System</option>
                </select>
              </div>
            </div>
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">Compact Mode</div>
                <div class="settings-row-desc">Reduce padding for more data density</div>
              </div>
              <div class="settings-row-action">
                <label class="toggle"><input type="checkbox"><span class="toggle-slider"></span></label>
              </div>
            </div>
          </div>
        </section>

        <!-- API Keys -->
        <section class="settings-section" id="section-api">
          <h3 class="settings-section-title">API Keys</h3>
          <p class="settings-section-desc">Manage API keys for external integrations.</p>
          <div class="settings-group">
            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">API Key</div>
                <div class="api-key-display" style="margin-top:var(--space-2)">
                  <span class="api-key-value" id="api-key-val">sk-••••••••••••••••••••3f2a</span>
                  <button class="btn btn-ghost btn-sm" id="toggle-key">${Utils.icon('eye')}</button>
                  <button class="btn btn-ghost btn-sm">${Utils.icon('copy')}</button>
                </div>
              </div>
              <div class="settings-row-action">
                <button class="btn btn-secondary btn-sm">Regenerate</button>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  `;

  // Settings nav interaction
  container.querySelectorAll('.settings-nav-item').forEach(item => {
    item.addEventListener('click', () => {
      container.querySelectorAll('.settings-nav-item').forEach(i => i.classList.remove('active'));
      item.classList.add('active');
      const section = document.getElementById('section-' + item.dataset.section);
      if (section) section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });

  // API key toggle
  const toggleBtn = container.querySelector('#toggle-key');
  const keyVal = container.querySelector('#api-key-val');
  let keyVisible = false;
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      keyVisible = !keyVisible;
      keyVal.textContent = keyVisible ? 'sk-a8b2c4d6e8f0g2h4i6j8k0l2n4p6r8t0v2x4z6a8c0e2g4i6k8m0o2q4s6u8w0y2a4c6e8g0i2k4m6o8q0r2t4v6x8z0b2d4f6h0j2l4n6p8r0t2v4x6z8a2c4e6g8i0k2m4o6q8s0u2w4y6z83f2a' : 'sk-••••••••••••••••••••3f2a';
      toggleBtn.innerHTML = Utils.icon(keyVisible ? 'eye-off' : 'eye');
    });
  }
}

window.renderSettings = renderSettings;
