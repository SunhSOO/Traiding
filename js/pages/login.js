/**
 * Login page. Posts to /api/auth/login (OAuth2 password flow),
 * stores the returned JWT in AppState, fetches /me, and routes
 * back to the originally-requested page (or /dashboard).
 */

function renderLogin(container) {
  const intended = sessionStorage.getItem('woonam.intended_route') || '/dashboard';

  container.innerHTML = `
    <div class="login-shell">
      <div class="login-card card">
        <div class="login-brand">
          <div class="sidebar-logo" style="width:48px;height:48px;font-size:24px">w</div>
          <div>
            <div class="login-title">woonam-auto-trading</div>
            <div class="login-sub">KR + US 복합 분석 자동매매</div>
          </div>
        </div>

        <form id="login-form" autocomplete="on">
          <div class="form-row">
            <label for="login-username">사용자명</label>
            <input id="login-username" name="username" type="text" required autofocus>
          </div>
          <div class="form-row">
            <label for="login-password">비밀번호</label>
            <input id="login-password" name="password" type="password" required>
          </div>
          <button type="submit" class="btn btn-primary login-submit" id="login-submit">
            로그인
          </button>
          <div class="login-error" id="login-error" hidden></div>
        </form>
      </div>
    </div>
  `;

  const form = container.querySelector('#login-form');
  const errorEl = container.querySelector('#login-error');
  const submitBtn = container.querySelector('#login-submit');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorEl.hidden = true;
    submitBtn.disabled = true;
    submitBtn.textContent = '인증 중…';

    const username = container.querySelector('#login-username').value.trim();
    const password = container.querySelector('#login-password').value;

    try {
      const tokenResp = await Api.login(username, password);
      AppState.setAuth({ token: tokenResp.access_token });
      // Best-effort fetch user profile; failures here are not fatal —
      // the token is valid, the user can navigate.
      try {
        const me = await Api.me();
        AppState.setAuth({ token: tokenResp.access_token, user: me });
      } catch { /* swallow */ }

      sessionStorage.removeItem('woonam.intended_route');
      AppRouter.navigate(intended);
    } catch (err) {
      errorEl.hidden = false;
      errorEl.textContent = err instanceof ApiError
        ? `로그인 실패: ${err.message}`
        : `오류: ${err.message}`;
      submitBtn.disabled = false;
      submitBtn.textContent = '로그인';
    }
  });
}

window.renderLogin = renderLogin;
