const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); tg.setHeaderColor?.('#0f0a19'); tg.setBackgroundColor?.('#0f0a19'); }

const token = new URLSearchParams(location.search).get('t') || '';
const initData = tg?.initData || '';
let authClient = null;
let authToken = '';
let authSession = null;
let authMode = 'signin';
let viewOwnerId = null;
const authHeaders = () => ({
  ...(initData ? {'X-Telegram-Init-Data': initData} : {}),
  ...((authToken || token) ? {'Authorization': `Bearer ${authToken || token}`} : {}),
});
const $ = (id) => document.getElementById(id);
let activeKind = 'expense';
let activePeriod = 'month';
let customStart = '';
let customEnd = '';
const currencySymbols = {KZT:'₸', RUB:'₽', USD:'$'};
const accountTypeMeta = {
  card:{icon:'💳',label:'Карта'},
  deposit:{icon:'🏦',label:'Депозит'},
  cash:{icon:'💵',label:'Наличные'},
};

const mobileDate = new Intl.DateTimeFormat('ru-RU', {month:'long', year:'numeric'}).format(new Date());
$('mobileDate').textContent = mobileDate.charAt(0).toUpperCase() + mobileDate.slice(1);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {...authHeaders(), ...(options.body ? {'Content-Type':'application/json'} : {}), ...(options.headers || {})},
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Не удалось выполнить запрос');
  return data;
}

function renderTelegramLogin(botUsername) {
  if (!botUsername || initData) return;
  const normalizedUsername = String(botUsername).replace(/^@/, '');
  $('telegramMobileLogin').onclick = async () => {
    const button = $('telegramMobileLogin');
    button.disabled = true;
    $('telegramLoginStatus').textContent = 'Создаём безопасную ссылку…';
    try {
      const response = await fetch('/api/auth/telegram/start', {method:'POST'});
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.telegram_url) throw new Error(data.detail || 'Не удалось открыть Telegram');
      $('telegramLoginStatus').textContent = 'Подтвердите вход в чате с ботом';
      location.href = data.telegram_url;
    } catch (error) {
      $('telegramLoginStatus').textContent = error.message || String(error);
      button.disabled = false;
    }
  };
  const container = $('telegramLoginWidget');
  container.replaceChildren();
  const script = document.createElement('script');
  script.async = true;
  script.src = 'https://telegram.org/js/telegram-widget.js?22';
  script.dataset.telegramLogin = normalizedUsername;
  script.dataset.size = 'large';
  script.dataset.radius = '12';
  script.dataset.onauth = 'onTelegramAuth(user)';
  container.appendChild(script);
  $('telegramLoginBlock').classList.remove('hidden');
}

window.onTelegramAuth = async user => {
  $('telegramLoginStatus').textContent = 'Входим через Telegram…';
  try {
    const response = await fetch('/api/auth/telegram', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(user),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Не удалось войти через Telegram');
    $('telegramLoginStatus').textContent = '✓ Готово';
    await refresh();
  } catch (error) {
    $('telegramLoginStatus').textContent = error.message || String(error);
  }
};

function formatDate(value) {
  const date = new Date(value);
  return new Intl.DateTimeFormat('ru-RU', {day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit'}).format(date);
}

function renderDashboard(data) {
  const firstCurrency = data.currency_totals?.[0] || {balance:data.balance,income:data.income,expense:data.expense,currency:'KZT'};
  $('balance').textContent = firstCurrency.balance;
  $('income').textContent = `+${firstCurrency.income}`;
  $('expense').textContent = `−${firstCurrency.expense}`;
  $('periodLabel').textContent = data.period_label.toLowerCase();
  $('historyTitle').textContent = data.period_label;
  $('currencyTotals').innerHTML = (data.currency_totals || []).map(item => `<div class="currency-total"><strong>${escapeHtml(item.balance)}</strong><small>${escapeHtml(item.currency)} · +${escapeHtml(item.income)} / −${escapeHtml(item.expense)}</small></div>`).join('');
  $('accounts').innerHTML = data.accounts.length ? data.accounts.map(item => {
    const meta = accountTypeMeta[item.account_type] || accountTypeMeta.card;
    const depositDetails = item.account_type === 'deposit'
      ? ` · ном. ${escapeHtml(item.nominal_interest_rate || item.interest_rate || '—')} · эфф. ${escapeHtml(item.effective_interest_rate || '—')}${item.daily_interest_enabled ? ' · ежедневно' : ''}`
      : '';
    return `<article class="account-card"><span>${meta.icon} ${escapeHtml(item.name)}</span><strong>${escapeHtml(item.balance)}</strong><small>${meta.label}${depositDetails} · ${escapeHtml(item.currency)}</small></article>`;
  }).join('') : '<div class="empty">Счета пока не созданы</div>';
  const max = Math.max(...data.top_categories.map(x => Number(x.amount_minor) || 0), 1);
  $('topCategories').innerHTML = data.top_categories.length ? data.top_categories.map(item => {
    const value = Number(item.amount_minor) || 0;
    return `<div class="chart-row"><div class="chart-meta"><span>${escapeHtml(item.label)}</span><span>${escapeHtml(item.amount)}</span></div><div class="chart-track"><div class="chart-fill" style="width:${Math.max(7,value/max*100)}%"></div></div></div>`;
  }).join('') : '<div class="empty">Расходов в этом месяце ещё нет</div>';
  $('history').innerHTML = data.history.length ? data.history.map(item => `
    <article class="history-item">
      <div class="history-icon">${escapeHtml(item.category.split(' ')[0])}</div>
      <div class="history-copy"><strong>${escapeHtml(item.category)}</strong><small>${escapeHtml(item.account)} · ${formatDate(item.created_at)}${item.note ? ` · ${escapeHtml(item.note)}` : ''}</small></div>
      <div class="history-amount ${item.kind}">${item.kind === 'income' ? '+' : '−'}${escapeHtml(item.amount)}</div>
    </article>`).join('') : '<div class="empty">Добавьте первую операцию — она появится здесь</div>';
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}

async function refresh() {
  try {
    const params = new URLSearchParams({period:activePeriod});
    if (activePeriod === 'custom') { params.set('start', customStart); params.set('end', customEnd); }
    if (viewOwnerId !== null) params.set('owner_id', String(viewOwnerId));
    const data = await api(`/api/dashboard?${params}`);
    renderDashboard(data);
    $('loading').classList.add('hidden'); $('authError').classList.add('hidden'); $('dashboard').classList.remove('hidden');
    $('mobileNav').classList.remove('hidden');
    $('addTransaction').classList.toggle('hidden', viewOwnerId !== null);
    $('mobileAddTransaction').classList.toggle('hidden', viewOwnerId !== null);
    $('newAccount').classList.toggle('hidden', viewOwnerId !== null);
    document.querySelector('.status-pill').classList.add('ready'); $('connectionLabel').textContent = viewOwnerId === null ? 'Всё сохранено' : `Просмотр ID ${viewOwnerId}`;
    try {
      const me = await api('/api/me');
      $('adminButton').classList.toggle('hidden', !me.is_admin);
      $('signOutButton').classList.toggle('hidden', !(authSession || me.can_sign_out));
    } catch (_) {}
  } catch (error) {
    $('loading').classList.add('hidden'); $('authError').classList.remove('hidden'); $('dashboard').classList.add('hidden'); $('addTransaction').classList.add('hidden'); $('mobileNav').classList.add('hidden'); $('connectionLabel').textContent = 'Нужен вход';
  }
}

async function loadOptions() {
  const data = await api(`/api/options?kind=${activeKind}`);
  $('categorySelect').innerHTML = '<option value="">Выберите категорию</option>' + data.categories.map(x => `<option value="${escapeHtml(x.value)}">${escapeHtml(x.label)}</option>`).join('');
  $('accountSelect').innerHTML = '<option value="">Выберите счёт</option>' + data.accounts.map(x => `<option value="${x.id}" data-currency="${escapeHtml(x.currency)}">${escapeHtml(x.name)} · ${escapeHtml(x.balance)}</option>`).join('');
  $('transactionCurrencySymbol').textContent = '₸';
}

$('accountSelect').onchange = () => {
  const currency = $('accountSelect').selectedOptions[0]?.dataset.currency || 'KZT';
  $('transactionCurrencySymbol').textContent = currencySymbols[currency] || currency;
};

function setDrawer(open) {
  $('transactionDrawer').classList.toggle('open', open); $('transactionDrawer').setAttribute('aria-hidden', String(!open)); $('drawerBackdrop').classList.toggle('hidden', !open);
  if (open) { loadOptions().catch(showFormError); setTimeout(() => $('amountInput').focus(), 280); }
}

function showFormError(error) { $('formStatus').textContent = error.message || String(error); tg?.HapticFeedback?.notificationOccurred('error'); }

$('addTransaction').onclick = () => setDrawer(true); $('closeDrawer').onclick = () => setDrawer(false); $('drawerBackdrop').onclick = () => setDrawer(false);
$('mobileAddTransaction').onclick = () => setDrawer(true);
document.querySelectorAll('[data-scroll-to]').forEach(button => button.onclick = () => {
  const target = $(button.dataset.scrollTo);
  if (!target) return;
  document.querySelectorAll('[data-scroll-to]').forEach(item => item.classList.toggle('active', item === button));
  target.scrollIntoView({behavior:'smooth', block:'start'});
});
document.querySelectorAll('[data-kind]').forEach(button => button.onclick = () => {
  activeKind = button.dataset.kind; document.querySelectorAll('[data-kind]').forEach(x => x.classList.toggle('active', x === button)); loadOptions().catch(showFormError);
});

$('transactionForm').onsubmit = async (event) => {
  event.preventDefault(); const button = $('saveTransaction'); button.disabled = true; $('formStatus').textContent = 'Сохраняем…';
  try {
    await api('/api/transactions', {method:'POST', body:JSON.stringify({kind:activeKind, amount:$('amountInput').value, category:$('categorySelect').value, account_id:$('accountSelect').value, note:$('noteInput').value})});
    tg?.HapticFeedback?.notificationOccurred('success'); $('transactionForm').reset(); $('formStatus').textContent = '✓ Операция сохранена'; await refresh(); setTimeout(() => setDrawer(false), 650);
  } catch (error) { showFormError(error); } finally { button.disabled = false; }
};

function openDialog(mode) {
  const account = mode === 'account'; $('dialogTitle').textContent = account ? 'Новый счёт' : 'Новая категория';
  ['dialogBalanceLabel','dialogTypeLabel','dialogCurrencyLabel'].forEach(id => $(id).classList.toggle('hidden', !account));
  ['dialogInterestLabel','dialogEffectiveInterestLabel','dialogDailyInterestLabel'].forEach(id => $(id).classList.add('hidden')); $('dialogName').value = ''; $('dialogBalance').value = '0'; $('dialogInterest').value = ''; $('dialogEffectiveInterest').value = ''; $('dialogDailyInterest').checked = false; $('dialogType').value = 'card'; $('dialogCurrency').value = 'KZT'; $('dialogCurrencySymbol').textContent = '₸'; $('dialogStatus').textContent = ''; $('smallDialog').dataset.mode = mode; $('smallDialog').showModal(); setTimeout(() => $('dialogName').focus(), 80);
}
$('newAccount').onclick = () => openDialog('account'); $('newCategory').onclick = () => openDialog('category');
$('closeSmallDialog').onclick = () => $('smallDialog').close();
$('dialogType').onchange = () => {
  const hidden = $('dialogType').value !== 'deposit';
  ['dialogInterestLabel','dialogEffectiveInterestLabel','dialogDailyInterestLabel'].forEach(id => $(id).classList.toggle('hidden', hidden));
};
$('dialogCurrency').onchange = () => {
  const currency = $('dialogCurrency').value;
  $('dialogCurrencySymbol').textContent = currencySymbols[currency] || currency;
};
$('smallDialogForm').onsubmit = async (event) => {
  event.preventDefault(); const account = $('smallDialog').dataset.mode === 'account'; $('dialogStatus').textContent = 'Сохраняем…';
  try {
    await api(account ? '/api/accounts' : '/api/categories', {method:'POST', body:JSON.stringify(account ? {name:$('dialogName').value,balance:$('dialogBalance').value,account_type:$('dialogType').value,currency:$('dialogCurrency').value,nominal_interest_rate:$('dialogInterest').value,effective_interest_rate:$('dialogEffectiveInterest').value,daily_interest_enabled:$('dialogDailyInterest').checked} : {name:$('dialogName').value,kind:activeKind})});
    $('smallDialog').close(); tg?.HapticFeedback?.notificationOccurred('success'); await refresh(); if (!account) await loadOptions();
  } catch (error) { event.preventDefault(); $('dialogStatus').textContent = error.message; }
};

document.querySelectorAll('[data-period]').forEach(button => button.onclick = async () => {
  const period = button.dataset.period;
  if (period === 'custom') { $('customPeriod').classList.remove('hidden'); return; }
  activePeriod = period; $('customPeriod').classList.add('hidden'); document.querySelectorAll('[data-period]').forEach(x => x.classList.toggle('active', x === button)); await refresh();
});
$('applyPeriod').onclick = async () => {
  customStart = $('periodStart').value; customEnd = $('periodEnd').value;
  if (!customStart || !customEnd) { tg?.showAlert?.('Выберите обе даты'); return; }
  activePeriod = 'custom'; document.querySelectorAll('[data-period]').forEach(x => x.classList.toggle('active', x.dataset.period === 'custom'));
  try { await refresh(); } catch (error) { tg?.showAlert?.(error.message); }
};

async function openUpdates() {
  $('updatesDialog').showModal();
  try {
    const data = await api('/api/whats-new');
    $('updatesList').innerHTML = data.releases.map(release => `<article class="release-card"><h4>Версия ${escapeHtml(release.version)}</h4><time>${escapeHtml(release.date)}</time><p>${escapeHtml(release.title)}</p><ul>${release.items.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></article>`).join('');
  } catch (error) { $('updatesList').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`; }
}
$('whatsNewButton').onclick = openUpdates;
$('closeUpdates').onclick = () => $('updatesDialog').close();

async function refreshAuthToken() {
  if (!authClient) return false;
  const sessionResult = await authClient.getSession();
  authSession = sessionResult?.data?.user ? sessionResult.data : null;
  if (!authSession) { authToken = ''; return false; }
  const tokenResult = await authClient.token();
  authToken = tokenResult?.data?.token || '';
  return Boolean(authToken);
}

function setAuthMode(mode) {
  authMode = mode;
  document.querySelectorAll('[data-auth-mode]').forEach(button => button.classList.toggle('active', button.dataset.authMode === mode));
  $('authNameLabel').classList.toggle('hidden', mode !== 'signup');
  $('authPassword').autocomplete = mode === 'signup' ? 'new-password' : 'current-password';
  $('authSubmit').textContent = mode === 'signup' ? 'Создать аккаунт' : 'Войти';
  $('forgotPassword').classList.toggle('hidden', mode !== 'signin');
  $('authStatus').textContent = '';
}

document.querySelectorAll('[data-auth-mode]').forEach(button => button.onclick = () => setAuthMode(button.dataset.authMode));

$('authForm').onsubmit = async event => {
  event.preventDefault();
  if (!authClient) { $('authStatus').textContent = 'Вход по email пока недоступен'; return; }
  $('authSubmit').disabled = true; $('authStatus').textContent = authMode === 'signup' ? 'Создаём аккаунт…' : 'Входим…';
  try {
    const credentials = {email:$('authEmail').value.trim(), password:$('authPassword').value};
    const result = authMode === 'signup'
      ? await authClient.signUp.email({...credentials, name:$('authName').value.trim() || credentials.email.split('@')[0]})
      : await authClient.signIn.email(credentials);
    if (result?.error) throw new Error(result.error.message || 'Не удалось войти');
    await refreshAuthToken();
    if (!authToken) throw new Error('Подтвердите email, затем выполните вход');
    $('authStatus').textContent = '✓ Готово';
    await refresh();
  } catch (error) { $('authStatus').textContent = error.message || String(error); }
  finally { $('authSubmit').disabled = false; }
};

$('forgotPassword').onclick = async () => {
  const email = $('authEmail').value.trim();
  if (!email) { $('authStatus').textContent = 'Сначала укажите email'; return; }
  try {
    const result = await authClient.requestPasswordReset({email, redirectTo:`${location.origin}/?reset=1`});
    if (result?.error) throw new Error(result.error.message || 'Не удалось отправить письмо');
    $('authStatus').textContent = 'Письмо для восстановления отправлено';
  } catch (error) { $('authStatus').textContent = error.message || String(error); }
};

$('resetForm').onsubmit = async event => {
  event.preventDefault();
  const resetToken = new URLSearchParams(location.search).get('token') || '';
  try {
    const result = await authClient.resetPassword({newPassword:$('resetPassword').value, token:resetToken});
    if (result?.error) throw new Error(result.error.message || 'Не удалось изменить пароль');
    $('resetStatus').textContent = '✓ Пароль изменён. Теперь можно войти.';
    history.replaceState({}, '', '/');
  } catch (error) { $('resetStatus').textContent = error.message || String(error); }
};
$('closeReset').onclick = () => $('resetDialog').close();

$('signOutButton').onclick = async () => {
  if (authClient) await authClient.signOut();
  await fetch('/api/auth/telegram/logout', {method:'POST'}).catch(() => {});
  authSession = null; authToken = ''; viewOwnerId = null;
  await refresh();
};

async function openAdmin() {
  $('adminDialog').showModal(); $('adminUsers').innerHTML = '<div class="empty">Загружаем пользователей…</div>';
  try {
    const data = await api('/api/admin/users');
    $('adminUsers').innerHTML = '<button class="admin-user-card" data-owner="self"><strong>↩ Мои данные</strong><small>Вернуться к собственной сводке</small></button>' + data.users.map(user => {
      const title = user.display_name || (user.username ? `@${user.username}` : `ID ${user.user_id}`);
      const accounts = user.accounts.length ? user.accounts.map(account => `${accountTypeMeta[account.account_type]?.icon || '💳'} ${escapeHtml(account.name)} — ${escapeHtml(account.balance)}`).join('<br>') : 'Счетов пока нет';
      return `<button class="admin-user-card" data-owner="${user.user_id}"><strong>${escapeHtml(title)}</strong><small>${accounts}</small></button>`;
    }).join('');
    document.querySelectorAll('.admin-user-card').forEach(button => button.onclick = async () => {
      viewOwnerId = button.dataset.owner === 'self' ? null : Number(button.dataset.owner);
      $('adminDialog').close(); await refresh();
    });
  } catch (error) { $('adminUsers').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`; }
}
$('adminButton').onclick = openAdmin;
$('closeAdmin').onclick = () => $('adminDialog').close();

async function boot() {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  const loginRequest = new URLSearchParams(location.search).get('login_request');
  if (loginRequest && initData) {
    try {
      await fetch('/api/auth/telegram/approve', {
        method:'POST',
        headers:{'Content-Type':'application/json', 'X-Telegram-Init-Data':initData},
        body:JSON.stringify({token:loginRequest}),
      });
      const cleanUrl = new URL(location.href);
      cleanUrl.searchParams.delete('login_request');
      history.replaceState({}, '', cleanUrl.pathname + cleanUrl.search + cleanUrl.hash);
    } catch (_) {}
  }
  try {
    const config = await fetch('/api/config').then(response => response.json());
    if (config.auth_enabled && window.PurposeAuthBundle?.createClient) {
      authClient = window.PurposeAuthBundle.createClient(config.auth_url);
      await refreshAuthToken();
    }
    if (config.telegram_login_enabled) renderTelegramLogin(config.telegram_bot_username);
  } catch (_) {}
  if (new URLSearchParams(location.search).get('reset') === '1' || new URLSearchParams(location.search).get('token')) {
    $('resetDialog').showModal();
  }
  await refresh();
}

boot();
