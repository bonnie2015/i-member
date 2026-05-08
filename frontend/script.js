/**
 * script.js — 品牌伙伴智能客服前端
 *
 * 交互状态机：
 *   idle      → 普通输入
 *   select    → 显示可点击选项卡片（点击回传 option.value）
 *   waiting   → 请求中，禁用所有输入
 */

const API_ORIGIN = window.location.protocol === 'file:' ? 'http://localhost:8000' : 'http://localhost:8000';
const API = `${API_ORIGIN}/api/v1`;
const MOCK_PROXY_ORIGIN = window.location.protocol === 'file:' ? 'http://localhost:3000' : '';
const MOCK_USER_ID = 'bonnie20260412';
const MOCK_JWT_ISS = 'member-ops-agent';
const MOCK_JWT_SECRET = 'change-me-in-production';
const MOCK_TOKEN_KEY = 'member_ops_mock_token';

// ── DOM refs ──────────────────────────────────────────────────
const $messages      = document.getElementById('messages');
const $messageInput  = document.getElementById('messageInput');
const $sendBtn       = document.getElementById('sendBtn');
const $charCount     = document.getElementById('charCount');
const $inputHint     = document.getElementById('inputHint');
const $selectPanel   = document.getElementById('selectPanel');
const $selectLabel   = document.getElementById('selectLabel');
const $optionCards   = document.getElementById('optionCards');
const $statusDot     = document.getElementById('statusDot');
const $statusText    = document.getElementById('statusText');
const $threadLabel   = document.getElementById('threadLabel');
const $memberLevel   = document.getElementById('memberLevel');
const $memberPoints  = document.getElementById('memberPoints');
const $memberName    = document.getElementById('memberName');
const $newChatBtn    = document.getElementById('newChatBtn');
const $welcomeGreeting = document.getElementById('welcomeGreeting');
const $welcomeFollowup = document.getElementById('welcomeFollowup');
const $chatTabBtn = document.getElementById('chatTabBtn');
const $mockTabBtn = document.getElementById('mockTabBtn');
const $chatView = document.getElementById('chatView');
const $mockDataView = document.getElementById('mockDataView');
const $mockRefreshBtn = document.getElementById('mockRefreshBtn');
const $mockResetBtn = document.getElementById('mockResetBtn');
const $mockStatus = document.getElementById('mockStatus');
const $mockSummaryGrid = document.getElementById('mockSummaryGrid');
const $mockOrders = document.getElementById('mockOrders');
const $mockTickets = document.getElementById('mockTickets');
const $mockUserPanel = document.getElementById('mockUserPanel');
const $mockOrderCount = document.getElementById('mockOrderCount');
const $mockTicketCount = document.getElementById('mockTicketCount');
const $mockTicketModal = document.getElementById('mockTicketModal');
const $mockTicketModalBackdrop = document.getElementById('mockTicketModalBackdrop');
const $mockTicketModalClose = document.getElementById('mockTicketModalClose');
const $mockTicketModalContent = document.getElementById('mockTicketModalContent');

// ── State ─────────────────────────────────────────────────────
let threadId   = null;
let uiState    = 'idle'; // idle | select | waiting
let latestThreadLoaded = false;
let mockDataLoaded = false;
let mockTickets = [];

// ── Init ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    syncThreadLabel();
    checkHealth();
    ensureMockToken().catch(err => console.error('mock token init failed', err));
    bindEvents();
});

function bindEvents() {
    $sendBtn.addEventListener('click', onSend);
    $messageInput.addEventListener('input', onInput);
    $messageInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); }
    });
    $newChatBtn.addEventListener('click', newChat);
    $chatTabBtn.addEventListener('click', () => switchView('chat'));
    $mockTabBtn.addEventListener('click', () => switchView('mock'));
    $mockRefreshBtn.addEventListener('click', () => loadMockData());
    $mockResetBtn.addEventListener('click', () => resetMockData());
    $mockTicketModalBackdrop.addEventListener('click', closeMockTicketModal);
    $mockTicketModalClose.addEventListener('click', closeMockTicketModal);
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && !$mockTicketModal.classList.contains('hidden')) {
            closeMockTicketModal();
        }
    });
    $mockTickets.addEventListener('click', event => {
        const target = event.target.closest('[data-ticket-id]');
        if (!target) return;
        openMockTicketModal(target.dataset.ticketId);
    });
    $mockOrders.addEventListener('click', event => {
        if (event.target.closest('a')) return;
        const target = event.target.closest('[data-order-card]');
        if (!target) return;
        target.classList.toggle('expanded');
    });
    document.querySelectorAll('.quick-btn').forEach(btn => {
        btn.addEventListener('click', () => sendText(btn.dataset.msg));
    });
}

function switchView(view) {
    const isMock = view === 'mock';
    $chatView.classList.toggle('hidden', isMock);
    $mockDataView.classList.toggle('hidden', !isMock);
    $chatTabBtn.classList.toggle('active', !isMock);
    $mockTabBtn.classList.toggle('active', isMock);
    $chatTabBtn.setAttribute('aria-selected', String(!isMock));
    $mockTabBtn.setAttribute('aria-selected', String(isMock));
    $newChatBtn.classList.toggle('hidden', isMock);
    if (isMock && !mockDataLoaded) {
        loadMockData();
    }
    if (!isMock) {
        $messageInput.focus();
    }
}

// ── Health check ──────────────────────────────────────────────
async function checkHealth() {
    try {
        const r = await fetch(`${API_ORIGIN}/health`);
        if (r.ok) setOnline(true);
        else      setOnline(false);
    } catch {
        setOnline(false);
    }
}

function setOnline(ok) {
    $statusDot.className  = 'dot ' + (ok ? 'online' : 'offline');
    $statusText.textContent = ok ? '服务正常' : '服务不可用';
    if (ok) {
        $sendBtn.disabled = false;
        $messageInput.disabled = false;
        $messageInput.focus();
        loadUserContext();
        restoreLatestThread().catch(err => console.error('restore latest thread failed', err));
    } else {
        $sendBtn.disabled = true;
        $messageInput.placeholder = '后端服务不可用，请检查连接...';
    }
}

// ── Load user context (sidebar member card) ───────────────────
async function loadUserContext() {
    try {
        const displayName = getDisplayName(MOCK_USER_ID);
        $memberLevel.textContent  = '黄金会员';
        $memberPoints.textContent = '积分：8500 → j还需 1500';
        $memberName.textContent   = '用户：' + displayName;
        updateWelcomeCopy(displayName);
    } catch { /* ignore */ }
}

function getDisplayName(userId) {
    const cleaned = String(userId || '')
        .replace(/\d+/g, '')
        .replace(/[_-]+/g, ' ')
        .trim();
    if (!cleaned) return '朋友';
    return cleaned
        .split(/\s+/)
        .map(part => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}

function updateWelcomeCopy(name) {
    if ($welcomeGreeting) {
        $welcomeGreeting.textContent = `Hello ${name}～`;
    }
    if ($welcomeFollowup) {
        $welcomeFollowup.textContent = '今天也该是很有格调的一天。有没有穿上最喜欢的那双鞋出门散步？\n\n如果你想看看新鞋、聊聊穿搭，或顺手处理会员服务，也可以和我聊聊，让我把最新的咨询和第一手品牌动态带给你～';
    }
}

function createWelcomeMessage() {
    const displayName = getDisplayName(MOCK_USER_ID);
    const row = document.createElement('div');
    row.className = 'welcome-msg';
    row.innerHTML = `
        <div class="welcome-avatar">BP</div>
        <div class="welcome-text">
            <p>Hello ${escHtml(displayName)}～</p>
            <p>今天也该是很有格调的一天。有没有穿上最喜欢的那双鞋出门散步？<br><br>如果你想看看新鞋、聊聊穿搭，或顺手处理会员服务，也可以和我聊聊，让我把最新的咨询和第一手品牌动态带给你～</p>
        </div>
    `;
    return row;
}

// ── Input handling ────────────────────────────────────────────
function onInput() {
    const len = $messageInput.value.length;
    $charCount.textContent = len + ' / 500';
    autoGrow($messageInput);
    $sendBtn.disabled = len === 0 || uiState === 'waiting';
}

function autoGrow(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// ── Send message ──────────────────────────────────────────────
function onSend() {
    const text = $messageInput.value.trim();
    if (!text || uiState === 'waiting') return;
    $messageInput.value = '';
    onInput();
    sendText(text);
}

async function sendText(text) {
    if (uiState === 'waiting') return;

    addUserBubble(text);
    setUiState('waiting');

    const thinkingId = addThinkingBubble('正在处理...');

    try {
        const body = { message: text, channel: 'web' };
        if (threadId) body.thread_id = threadId;
        const token = await ensureMockToken();

        const res = await fetch(`${API}/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`,
            },
            body: JSON.stringify(body),
        });

        const data = await safeReadJson(res);
        if (!res.ok) {
            throw new Error(formatApiError(res.status, data));
        }
        const normalized = normalizeChatResponse(data);
        threadId = normalized.thread_id || threadId;
        syncThreadLabel();
        removeThinkingBubble(thinkingId);

        let botRow = null;
        if (normalized.reply || normalized.products.length > 0 || normalized.interaction) {
            botRow = addBotBubble(normalized.reply, normalized.products);
        }
        if (normalized.interaction) {
            handleInteraction(botRow, normalized.reply, normalized.interaction);
        } else {
            setUiState('idle');
            if (!normalized.reply && normalized.products.length === 0) {
                addBotBubble('系统已处理完成，但本轮没有返回可展示内容。您可以继续输入问题。');
            }
        }

    } catch (err) {
        removeThinkingBubble(thinkingId);
        addBotBubble(formatUserError(err));
        setUiState('idle');
        console.error(err);
    }
}

function normalizeChatResponse(payload) {
    const data = isObj(payload) ? payload : {};
    const thread = textOrEmpty(data.thread_id || data.threadId);
    const reply = pickText(data.reply, data.message, data.content);
    const interaction = normalizeInteraction(data.interaction);
    const products = normalizeProducts(data.products);
    return { thread_id: thread, reply, interaction, products };
}

function normalizeLatestThreadResponse(payload) {
    const data = isObj(payload) ? payload : {};
    const thread = textOrEmpty(data.thread_id || data.threadId);
    const messages = Array.isArray(data.messages)
        ? data.messages
            .map(item => ({
                role: textOrEmpty(item && item.role),
                content: textOrEmpty(item && item.content),
                products: normalizeProducts(item && item.products),
                interaction: normalizeInteraction(item && item.interaction),
            }))
            .filter(item => {
                if (item.role !== 'user' && item.role !== 'assistant') return false;
                if (item.role === 'assistant') return !!item.content || item.products.length > 0 || !!item.interaction;
                return !!item.content;
            })
        : [];
    return { thread_id: thread, messages };
}

function normalizeProducts(raw) {
    if (!Array.isArray(raw)) return [];
    return raw.map(normalizeProduct).filter(Boolean);
}

function normalizeProduct(item) {
    if (!isObj(item)) return null;
    const productId = Number(item.product_id || item.productId || 0);
    const name = pickText(item.name);
    if (!productId || !name) return null;
    const colorId = Number(item.color_id || item.colorId || 0) || null;
    return {
        product_id: productId,
        name,
        price: normalizePrice(item.price),
        image: pickText(item.image, item.cover),
        official_url: pickText(item.official_url, item.officialUrl) || buildOfficialProductUrl(productId, colorId),
        color_id: colorId,
        color_name: pickText(item.color_name, item.colorName),
        category: pickText(item.category),
        gender: pickText(item.gender),
        reason: pickText(item.reason),
        in_stock: typeof item.in_stock === 'boolean' ? item.in_stock : null,
        stock: Number.isFinite(Number(item.stock)) ? Number(item.stock) : null,
    };
}

function normalizePrice(value) {
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
}

function buildOfficialProductUrl(productId, colorId) {
    if (!productId || !colorId) return '';
    return `https://www.onitsukatiger.com/cn/zh-cn/detail/${productId}-${colorId}`;
}

function normalizeInteraction(raw) {
    if (!isObj(raw) || !Array.isArray(raw.items)) return null;
    const interactionType = textOrEmpty(raw.interaction_type || raw.type || raw.interactionType);
    const items = raw.items
        .map(normalizeInteractionItem)
        .filter(Boolean);
    if (items.length === 0) return null;
    return {
        interaction_type: interactionType,
        items,
    };
}

function normalizeInteractionItem(item) {
    if (!isObj(item)) return null;
    const detail = isObj(item.detail) ? item.detail : {};
    const key = pickText(item.key, item.value, item.id, detail.action);
    const label = pickText(item.label, item.text, key);
    if (!label || !key) return null;
    return {
        key,
        label,
        selectable: item.selectable !== false,
        detail,
    };
}

function pickText(...values) {
    for (const value of values) {
        const t = textOrEmpty(value);
        if (t) return t;
    }
    return '';
}

function textOrEmpty(value) {
    if (typeof value === 'string') return value.trim();
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    return '';
}

function isObj(value) {
    return !!value && typeof value === 'object' && !Array.isArray(value);
}

async function safeReadJson(res) {
    try {
        return await res.json();
    } catch {
        return null;
    }
}

function formatApiError(status, payload) {
    const prefix = `HTTP ${status}`;
    if (!isObj(payload)) return prefix;
    if (typeof payload.detail === 'string' && payload.detail.trim()) {
        return `${prefix}: ${payload.detail.trim()}`;
    }
    if (Array.isArray(payload.detail) && payload.detail.length > 0) {
        const first = payload.detail[0];
        if (isObj(first)) {
            const msg = pickText(first.msg, first.message, first.type);
            if (msg) return `${prefix}: ${msg}`;
        }
    }
    const msg = pickText(payload.message, payload.error, payload.reply);
    return msg ? `${prefix}: ${msg}` : prefix;
}

function formatUserError(err) {
    const msg = pickText(err && err.message);
    if (!msg) return '抱歉，出了点小问题，请稍后再试。';
    return `请求失败：${msg}`;
}

// ── Mock auth token ──────────────────────────────────────────
function base64urlFromBytes(bytes) {
    let binary = '';
    for (const b of bytes) binary += String.fromCharCode(b);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function base64urlFromString(str) {
    return base64urlFromBytes(new TextEncoder().encode(str));
}

async function hmacSha256(message, secret) {
    const key = await crypto.subtle.importKey(
        'raw',
        new TextEncoder().encode(secret),
        { name: 'HMAC', hash: 'SHA-256' },
        false,
        ['sign']
    );
    const sig = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(message));
    return base64urlFromBytes(new Uint8Array(sig));
}

function parseJwtPayload(token) {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    try {
        const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
        const pad = payload.length % 4 ? '='.repeat(4 - payload.length % 4) : '';
        return JSON.parse(atob(payload + pad));
    } catch {
        return null;
    }
}

async function buildMockToken() {
    const now = Math.floor(Date.now() / 1000);
    const payload = {
        sub: MOCK_USER_ID,
        iss: MOCK_JWT_ISS,
        iat: now,
        exp: now + 7 * 24 * 3600,
    };
    const header = { alg: 'HS256', typ: 'JWT' };
    const h = base64urlFromString(JSON.stringify(header));
    const p = base64urlFromString(JSON.stringify(payload));
    const s = await hmacSha256(`${h}.${p}`, MOCK_JWT_SECRET);
    return `${h}.${p}.${s}`;
}

async function ensureMockToken() {
    const cached = localStorage.getItem(MOCK_TOKEN_KEY);
    if (cached) {
        const payload = parseJwtPayload(cached);
        if (payload && payload.sub === MOCK_USER_ID && payload.exp > Math.floor(Date.now() / 1000) + 60) {
            return cached;
        }
    }
    const token = await buildMockToken();
    localStorage.setItem(MOCK_TOKEN_KEY, token);
    return token;
}

async function restoreLatestThread() {
    if (latestThreadLoaded) return;
    latestThreadLoaded = true;

    try {
        const token = await ensureMockToken();
        const res = await fetch(`${API}/chat/latest-thread`, {
            method: 'GET',
            headers: {
                'Authorization': `Bearer ${token}`,
            },
        });

        const data = await safeReadJson(res);
        if (!res.ok) {
            throw new Error(formatApiError(res.status, data));
        }

        const normalized = normalizeLatestThreadResponse(data);
        if (!normalized.thread_id || normalized.messages.length === 0) {
            return;
        }

        const restoredInteraction = renderHistoryMessages(normalized.messages);
        threadId = normalized.thread_id;
        syncThreadLabel();
        if (!restoredInteraction) {
            setUiState('idle');
        }
    } catch (err) {
        console.error(err);
    }
}

// ── Mock data view ────────────────────────────────────────────
async function loadMockData() {
    setMockStatus('加载中...');
    try {
        const res = await fetch(`${MOCK_PROXY_ORIGIN}/mock-scrm/state`);
        const payload = await safeReadJson(res);
        if (!res.ok) throw new Error(formatApiError(res.status, payload));
        const state = payload && payload.state ? payload.state : {};
        mockDataLoaded = true;
        renderMockData(state, payload && payload.state_file);
    } catch (err) {
        console.error(err);
        setMockStatus(formatUserError(err));
    }
}

async function resetMockData() {
    setMockStatus('正在重置...');
    try {
        const res = await fetch(`${MOCK_PROXY_ORIGIN}/mock-scrm/reset`, { method: 'POST' });
        const payload = await safeReadJson(res);
        if (!res.ok) throw new Error(formatApiError(res.status, payload));
        const state = payload && payload.state ? payload.state : {};
        mockDataLoaded = true;
        renderMockData(state, payload && payload.state_file);
    } catch (err) {
        console.error(err);
        setMockStatus(formatUserError(err));
    }
}

function renderMockData(state, stateFile) {
    const orders = Array.isArray(state.orders)
        ? state.orders.filter(order => textOrEmpty(order.user_id) === MOCK_USER_ID)
        : [];
    const tickets = Array.isArray(state.tickets) ? state.tickets : [];
    mockTickets = tickets;
    const profile = extractProfile(state.user_profiles, MOCK_USER_ID);
    const score = isObj(state.score) && textOrEmpty(state.score.user_id) === MOCK_USER_ID ? state.score : {};
    const level = isObj(state.user_level) && textOrEmpty(state.user_level.user_id) === MOCK_USER_ID ? state.user_level : {};

    $mockOrderCount.textContent = `${orders.length} 条`;
    $mockTicketCount.textContent = `${tickets.length} 条`;
    $mockSummaryGrid.innerHTML = renderMockSummary(orders, tickets, score, level);
    $mockOrders.innerHTML = orders.length ? orders.map(renderMockOrder).join('') : renderMockEmpty('暂无订单');
    $mockTickets.innerHTML = tickets.length ? tickets.map(renderMockTicket).join('') : renderMockEmpty('暂无工单');
    $mockUserPanel.innerHTML = renderMockUser(profile, score, level);
    setMockStatus(stateFile ? `数据文件：${stateFile}` : '已加载');
}

function renderMockSummary(orders, tickets, score, level) {
    const unsigned = orders.filter(order => ['shipping', 'delivering'].includes(textOrEmpty(order.status))).length;
    const openTickets = tickets.filter(ticket => !['closed', 'done'].includes(textOrEmpty(ticket.status))).length;
    return [
        ['订单', orders.length],
        ['未签收', unsigned],
        ['工单', tickets.length],
        ['处理中', openTickets],
        ['积分', score.score_balance ?? '-'],
        ['等级', pickText(level.level, level.level_code, '-')],
    ].map(([label, value]) => `
        <div class="mock-summary-item">
            <span>${escHtml(String(label))}</span>
            <strong>${escHtml(String(value))}</strong>
        </div>
    `).join('');
}

function renderMockOrder(order) {
    const items = Array.isArray(order.items) ? order.items : [];
    return `
        <article class="mock-record mock-order-card" data-order-card>
            <div class="mock-record-head">
                <div>
                    <div class="mock-record-id">${escHtml(pickText(order.order_id))}</div>
                    <div class="mock-record-meta">${escHtml(pickText(order.created_at))} · ${escHtml(pickText(order.source_channel))}</div>
                </div>
                <span class="mock-chip">${escHtml(pickText(order.status_label, order.status))}</span>
            </div>
            <div class="mock-record-line">
                <span>${escHtml(pickText(order.items_summary))}</span>
                <strong>¥${escHtml(String(order.amount ?? '-'))}</strong>
            </div>
            <div class="mock-order-toggle">点击展开 ${items.length} 件商品明细</div>
            <div class="mock-order-detail">
                ${order.logistics ? renderMockLogistics(order.logistics) : ''}
                <div class="mock-product-grid">
                    ${items.map(item => renderMockOrderItem(item)).join('')}
                </div>
            </div>
        </article>
    `;
}

function renderMockLogistics(logistics) {
    if (!isObj(logistics)) return '';
    return `
        <div class="mock-logistics">
            <span>${escHtml(pickText(logistics.company))}</span>
            <span>${escHtml(pickText(logistics.latest_trace))}</span>
            <span>${escHtml(pickText(logistics.estimated_delivery_time))}</span>
        </div>
    `;
}

function renderMockOrderItem(item) {
    const image = escAttr(pickText(item.image));
    const name = escHtml(pickText(item.name));
    const url = escAttr(pickText(item.official_url));
    const body = `
        <div class="mock-product-image">
            ${image ? `<img src="${image}" alt="${name}">` : ''}
        </div>
        <div class="mock-product-copy">
            <div class="mock-product-name">${name}</div>
            <div class="mock-product-meta">ID ${escHtml(pickText(item.product_id))} · Color ${escHtml(pickText(item.color_id))}</div>
            <div class="mock-product-meta">${escHtml(pickText(item.style))} · ${escHtml(pickText(item.size))} · ×${escHtml(pickText(item.qty))}</div>
            <div class="mock-product-price">¥${escHtml(pickText(item.price))}</div>
        </div>
    `;
    return url
        ? `<a class="mock-product" href="${url}" target="_blank" rel="noopener noreferrer">${body}</a>`
        : `<div class="mock-product">${body}</div>`;
}

function renderMockTicket(ticket) {
    return `
        <button class="mock-ticket-row" type="button" data-ticket-id="${escAttr(pickText(ticket.ticket_id))}">
            <span>${escHtml(pickText(ticket.title, ticket.ticket_id))}</span>
            <strong>${escHtml(pickText(ticket.status_label, ticket.status))}</strong>
        </button>
    `;
}

function openMockTicketModal(ticketId) {
    const ticket = mockTickets.find(item => pickText(item.ticket_id) === ticketId);
    if (!ticket) return;
    $mockTicketModalContent.innerHTML = renderMockTicketDetail(ticket);
    $mockTicketModal.classList.remove('hidden');
    $mockTicketModal.setAttribute('aria-hidden', 'false');
    $mockTicketModalClose.focus();
}

function closeMockTicketModal() {
    $mockTicketModal.classList.add('hidden');
    $mockTicketModal.setAttribute('aria-hidden', 'true');
    $mockTicketModalContent.innerHTML = '';
}

function renderMockTicketDetail(ticket) {
    const timeline = Array.isArray(ticket.timeline) ? ticket.timeline : [];
    const images = Array.isArray(ticket.images) ? ticket.images : [];
    return `
        <div class="mock-modal-kicker">${escHtml(pickText(ticket.ticket_id))}</div>
        <h2 class="mock-modal-title" id="mockTicketModalTitle">${escHtml(pickText(ticket.title))}</h2>
        <div class="mock-modal-meta-row">
            <span class="mock-chip">${escHtml(pickText(ticket.status_label, ticket.status))}</span>
            <span>${escHtml(pickText(ticket.ticket_type))}</span>
            <span>${escHtml(pickText(ticket.source_channel))}</span>
        </div>
        <div class="mock-detail-grid">
            ${renderMockDetailItem('工单号', ticket.ticket_id)}
            ${renderMockDetailItem('订单号', ticket.order_id)}
            ${renderMockDetailItem('商品项', ticket.order_item_id)}
            ${renderMockDetailItem('创建时间', ticket.created_at)}
            ${renderMockDetailItem('预计完成', ticket.expected_finish_time)}
            ${renderMockDetailItem('最新进展', ticket.latest_progress)}
        </div>
        <section class="mock-detail-section">
            <h3>问题描述</h3>
            <p>${escHtml(pickText(ticket.description, ticket.content))}</p>
        </section>
        ${images.length ? `
            <section class="mock-detail-section">
                <h3>图片</h3>
                <div class="mock-detail-images">
                    ${images.map(url => `<a href="${escAttr(url)}" target="_blank" rel="noopener noreferrer">${escHtml(url)}</a>`).join('')}
                </div>
            </section>
        ` : ''}
        ${timeline.length ? `
            <section class="mock-detail-section">
                <h3>时间线</h3>
                <div class="mock-timeline">
                    ${timeline.map(item => `
                        <div class="mock-timeline-item">
                            <span>${escHtml(pickText(item.time))}</span>
                            <strong>${escHtml(pickText(item.action))}</strong>
                            <em>${escHtml(pickText(item.operator))}</em>
                        </div>
                    `).join('')}
                </div>
            </section>
        ` : ''}
    `;
}

function renderMockDetailItem(label, value) {
    const text = pickText(value);
    if (!text) return '';
    return `
        <div class="mock-detail-item">
            <span>${escHtml(label)}</span>
            <strong>${escHtml(text)}</strong>
        </div>
    `;
}

function renderMockUser(profile, score, level) {
    const basic = isObj(profile.basic_info) ? profile.basic_info : {};
    const value = isObj(profile.value_segment) ? profile.value_segment : {};
    const pref = isObj(profile.preferences) ? profile.preferences : {};
    const records = Array.isArray(score.records) ? score.records : [];
    return `
        <article class="mock-record">
            <div class="mock-record-head">
                <div>
                    <div class="mock-record-id">${escHtml(pickText(basic.name, '用户'))}</div>
                    <div class="mock-record-meta">${escHtml(pickText(basic.city))} · ${escHtml(pickText(basic.member_level, level.level))}</div>
                </div>
                <span class="mock-chip">${escHtml(pickText(level.level_code, level.level))}</span>
            </div>
            <div class="mock-user-grid">
                <div><span>积分</span><strong>${escHtml(pickText(score.score_balance))}</strong></div>
                <div><span>距下级</span><strong>${escHtml(pickText(level.score_to_next))}</strong></div>
                <div><span>客单价</span><strong>¥${escHtml(pickText(value.avg_order_value))}</strong></div>
                <div><span>RFM</span><strong>${escHtml(pickText(value.rfm_level))}</strong></div>
            </div>
            <div class="mock-record-desc">${escHtml(arrayText(pref.product_categories))} · ${escHtml(arrayText(pref.product_series))}</div>
            <div class="mock-score-list">
                ${records.slice(0, 4).map(item => `<div><span>${escHtml(pickText(item.reason))}</span><strong>${escHtml(pickText(item.change))}</strong></div>`).join('')}
            </div>
        </article>
    `;
}

function extractProfile(userProfiles, userId) {
    if (!isObj(userProfiles)) return {};
    const entry = userProfiles[userId];
    return entry && isObj(entry.profile) ? entry.profile : {};
}

function arrayText(value) {
    return Array.isArray(value) ? value.filter(Boolean).join(' / ') : pickText(value);
}

function renderMockEmpty(text) {
    return `<div class="mock-empty">${escHtml(text)}</div>`;
}

function setMockStatus(text) {
    $mockStatus.textContent = text;
}

function renderHistoryMessages(messages) {
    $messages.innerHTML = '';

    let rendered = 0;
    let latestAssistantHasInteraction = false;
    for (const message of messages) {
        if (!message) continue;
        if (message.role === 'user') {
            if (!message.content) continue;
            addUserBubble(message.content);
            rendered += 1;
            latestAssistantHasInteraction = false;
            continue;
        }
        if (message.role === 'assistant') {
            const botRow = addBotBubble(message.content, message.products || []);
            latestAssistantHasInteraction = false;
            if (message.interaction) {
                latestAssistantHasInteraction = handleInteraction(botRow, message.content, message.interaction);
            }
            rendered += 1;
        }
    }

    if (rendered === 0) {
        $messages.appendChild(createWelcomeMessage());
    }
    return latestAssistantHasInteraction;
}

// ── Interaction handling ──────────────────────────────────────
function handleInteraction(targetRow, reply, interaction) {
    const options = toSelectableOptions(interaction);
    if (options.length > 0) {
        const label = textOrEmpty(reply) ? '' : defaultInteractionLabel(interaction.interaction_type);
        appendInlineInteraction(targetRow, options, label);
        const hasSelectable = options.some(opt => opt.selectable !== false);
        setUiState(hasSelectable ? 'select' : 'idle');
        return hasSelectable;
    } else {
        setUiState('idle');
        $inputHint.textContent = '请继续回复';
        $messageInput.focus();
        return false;
    }
}

function toSelectableOptions(interaction) {
    if (!interaction || !Array.isArray(interaction.items)) return [];
    return interaction.items
        .filter(item => !!item)
        .map(item => ({
            label: String(item.label || item.key || ''),
            value: String(item.key || (item.detail && item.detail.action) || item.label || ''),
            detail: isObj(item.detail) ? item.detail : {},
            interactionType: String(interaction.interaction_type || ''),
            selectable: item.selectable !== false,
        }));
}

function defaultInteractionLabel(interactionType) {
    const labelByType = {
        select_order: '请选择要处理的订单',
        select_product: '请选择要处理的商品',
        select_ticket: '请选择要处理的工单',
        confirm_order: '请确认要处理的订单',
        confirm_product: '请确认要处理的商品',
        confirm_ticket: '请确认要处理的工单',
        confirm: '请确认下一步操作',
    };
    return labelByType[interactionType] || '请选择：';
}

function syncThreadLabel() {
    $threadLabel.textContent = threadId ? `会话ID：${threadId}` : '新会话';
}

// ── UI state machine ──────────────────────────────────────────
function setUiState(state, options = [], label = '请选择：') {
    uiState = state;

    $selectPanel.classList.add('hidden');

    $messageInput.disabled = false;
    $sendBtn.disabled = $messageInput.value.trim().length === 0;
    $inputHint.textContent = '';

    switch (state) {
        case 'waiting':
            $messageInput.disabled = true;
            $sendBtn.disabled = true;
            break;

        case 'select':
            $messageInput.focus();
            break;

        case 'idle':
        default:
            $messageInput.focus();
            break;
    }
}

function renderOptionCards(options) {
    $optionCards.innerHTML = '';
    options.forEach(opt => {
        const label = typeof opt === 'string' ? opt : String(opt.label || opt.value || '');
        const value = typeof opt === 'string' ? opt : String(opt.value || opt.label || '');
        const card = document.createElement('button');
        card.className = 'option-card';
        card.textContent = label;
        card.addEventListener('click', () => {
            card.classList.add('selected');
            sendText(value);
        });
        $optionCards.appendChild(card);
    });
}

function appendInlineInteraction(targetRow, options, label = '') {
    if (!targetRow || options.length === 0) return;

    const bubble = targetRow.querySelector('.bubble-with-cards, .bubble-cards-only, .bubble');
    if (!bubble) return;

    const existing = bubble.querySelector('.inline-interaction');
    if (existing) existing.remove();

    const host = document.createElement('div');
    host.className = 'inline-interaction';

    if (label) {
        const labelEl = document.createElement('div');
        labelEl.className = 'inline-interaction-label';
        labelEl.textContent = label;
        host.appendChild(labelEl);
    }

    const cards = document.createElement('div');
    cards.className = 'option-cards-inline';

    options.forEach(opt => {
        const labelText = typeof opt === 'string' ? opt : String(opt.label || opt.value || '');
        const value = typeof opt === 'string' ? opt : String(opt.value || opt.label || '');
        const selectable = !opt || typeof opt === 'string' ? true : opt.selectable !== false;
        const card = document.createElement(selectable ? 'button' : 'div');
        card.className = 'option-card option-card-inline';
        if (!selectable) card.classList.add('non-selectable');
        card.innerHTML = renderInteractionOptionContent(opt, labelText);
        if (selectable) {
            card.addEventListener('click', () => {
                card.classList.add('selected');
                sendText(value);
            });
        }
        cards.appendChild(card);
    });

    host.appendChild(cards);
    bubble.appendChild(host);
    scrollBottom();
}

function renderInteractionOptionContent(option, fallbackLabel) {
    if (!option || typeof option === 'string') {
        return `<span class="interaction-option-title">${escHtml(String(fallbackLabel || option || ''))}</span>`;
    }

    const detail = isObj(option.detail) ? option.detail : {};
    const kind = getInteractionDetailKind(option.interactionType, detail);

    if (kind === 'order') {
        return renderOrderInteractionCard(option, detail, fallbackLabel);
    }
    if (kind === 'product') {
        return renderProductInteractionCard(option, detail, fallbackLabel);
    }
    if (kind === 'ticket') {
        return renderTicketInteractionCard(option, detail, fallbackLabel);
    }
    return `<span class="interaction-option-title">${escHtml(String(fallbackLabel || option.label || option.value || ''))}</span>`;
}

function getInteractionDetailKind(interactionType, detail) {
    const type = String(interactionType || '').trim();
    if (type.includes('order')) return 'order';
    if (type.includes('product')) return 'product';
    if (type.includes('ticket')) return 'ticket';
    if (Array.isArray(detail.items_preview)) return 'order';
    if (detail.ticket_id || detail.ticket_type) return 'ticket';
    if (detail.product_id || detail.order_item_id || detail.sku_id) return 'product';
    if (detail.order_id) return 'order';
    return '';
}

function renderOrderInteractionCard(option, detail, fallbackLabel) {
    const title = escHtml(detail.order_id ? `订单 ${detail.order_id}` : String(fallbackLabel || option.label || ''));
    const status = escHtml(pickText(detail.order_status_label));
    const channel = escHtml(pickText(detail.source_channel));
    const previews = Array.isArray(detail.items_preview) ? detail.items_preview.slice(0, 3) : [];
    const previewHtml = previews.length > 0
        ? `
            <div class="interaction-preview-list">
                ${previews.map(renderOrderPreviewItem).join('')}
            </div>
        `
        : '';
    return `
        <div class="interaction-card interaction-card-order">
            <div class="interaction-card-head">
                <div class="interaction-card-title-wrap">
                    <div class="interaction-card-eyebrow">订单确认</div>
                    <div class="interaction-option-title">${title}</div>
                </div>
                ${status ? `<span class="interaction-chip">${status}</span>` : ''}
            </div>
            ${channel ? `<div class="interaction-meta">渠道 · ${channel}</div>` : ''}
            ${previewHtml}
        </div>
    `;
}

function renderOrderPreviewItem(item) {
    const data = isObj(item) ? item : {};
    const image = escAttr(pickText(data.product_image));
    const name = escHtml(pickText(data.product_name) || '商品');
    const qty = textOrEmpty(data.order_item_quantity) ? `×${escHtml(textOrEmpty(data.order_item_quantity))}` : '';
    return `
        <div class="interaction-preview-item">
            <div class="interaction-preview-thumb">
                ${image ? `<img src="${image}" alt="${name}">` : '<div class="interaction-preview-placeholder"></div>'}
            </div>
            <div class="interaction-preview-copy">
                <div class="interaction-preview-name">${name}</div>
                ${qty ? `<div class="interaction-preview-meta">${qty}</div>` : ''}
            </div>
        </div>
    `;
}

function renderProductInteractionCard(option, detail, fallbackLabel) {
    const image = escAttr(pickText(detail.product_image));
    const title = escHtml(pickText(detail.product_name, fallbackLabel, option.label, detail.product_id));
    const qty = textOrEmpty(detail.order_item_quantity);
    const orderId = escHtml(pickText(detail.order_id));
    const productId = escHtml(pickText(detail.product_id));
    return `
        <div class="interaction-card interaction-card-product">
            <div class="interaction-product-media">
                ${image ? `<img src="${image}" alt="${title}">` : '<div class="interaction-preview-placeholder"></div>'}
            </div>
            <div class="interaction-product-body">
                <div class="interaction-card-eyebrow">商品确认</div>
                <div class="interaction-option-title">${title}</div>
                <div class="interaction-meta-row">
                    ${productId ? `<span class="interaction-meta">商品ID · ${productId}</span>` : ''}
                    ${qty ? `<span class="interaction-chip">×${escHtml(qty)}</span>` : ''}
                </div>
                ${orderId ? `<div class="interaction-meta">订单 · ${orderId}</div>` : ''}
            </div>
        </div>
    `;
}

function renderTicketInteractionCard(option, detail, fallbackLabel) {
    const title = escHtml(pickText(detail.title, fallbackLabel, option.label));
    const status = escHtml(pickText(detail.status_label, detail.status));
    const ticketId = escHtml(pickText(detail.ticket_id));
    const ticketType = escHtml(pickText(detail.ticket_type));
    return `
        <div class="interaction-card interaction-card-ticket">
            <div class="interaction-card-head">
                <div class="interaction-card-title-wrap">
                    <div class="interaction-card-eyebrow">工单确认</div>
                    <div class="interaction-option-title">${title}</div>
                </div>
                ${status ? `<span class="interaction-chip">${status}</span>` : ''}
            </div>
            <div class="interaction-meta-row">
                ${ticketId ? `<span class="interaction-meta">工单号 · ${ticketId}</span>` : ''}
                ${ticketType ? `<span class="interaction-meta">类型 · ${ticketType}</span>` : ''}
            </div>
        </div>
    `;
}

// ── DOM helpers ───────────────────────────────────────────────
function addUserBubble(text) {
    const row = document.createElement('div');
    row.className = 'msg-row user';
    row.innerHTML = `
        <div class="bubble">${escHtml(text)}</div>
        <div class="msg-avatar">我</div>
    `;
    $messages.appendChild(row);
    scrollBottom();
}

function addBotBubble(text, products = []) {
    const row = document.createElement('div');
    row.className = 'msg-row bot';
    const formattedText = formatBotText(text);
    const hasText = !!textOrEmpty(text);
    const bubbleClass = hasText ? 'bubble bubble-with-cards' : 'bubble bubble-cards-only';

    row.innerHTML = `
        <div class="msg-avatar">B</div>
        <div class="${bubbleClass}">
            ${hasText ? `<div class="bot-text">${formattedText}</div>` : ''}
            ${products.length > 0 ? renderProductCards(products) : ''}
        </div>
    `;
    $messages.appendChild(row);
    scrollBottom();
    return row;
}

function renderProductCards(products) {
    return `<div class="product-card-list">${products.map(renderProductCard).join('')}</div>`;
}

function renderProductCard(product) {
    const url = escAttr(product.official_url || '#');
    const image = escAttr(product.image || '');
    const name = escHtml(product.name || '');
    const meta = [product.color_name, product.category || product.gender].filter(Boolean).join(' · ');
    const price = product.price != null ? `¥${Math.round(product.price)}` : '';
    const reason = escHtml(product.reason || '');
    return `
        <a class="product-card" href="${url}" target="_blank" rel="noopener noreferrer">
            <div class="product-card-image-wrap">
                ${image ? `<img class="product-card-image" src="${image}" alt="${name}">` : '<div class="product-card-image placeholder"></div>'}
            </div>
            <div class="product-card-body">
                <div class="product-card-name">${name}</div>
                ${meta ? `<div class="product-card-meta">${escHtml(meta)}</div>` : ''}
                <div class="product-card-price-row">
                    ${price ? `<span class="product-card-price">${price}</span>` : ''}
                </div>
                ${reason ? `<div class="product-card-reason">${reason}</div>` : ''}
            </div>
        </a>
    `;
}

function addThinkingBubble(text) {
    const id = 'thinking_' + Date.now();
    const row = document.createElement('div');
    row.className = 'msg-row bot';
    row.id = id;
    row.innerHTML = `
        <div class="msg-avatar">B</div>
        <div class="thinking-bubble">
            <span class="thinking-text">${escHtml(text)}</span>
            <span class="thinking-dot"></span>
            <span class="thinking-dot"></span>
            <span class="thinking-dot"></span>
        </div>
    `;
    $messages.appendChild(row);
    scrollBottom();
    return id;
}

function removeThinkingBubble(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function newChat() {
    threadId = null;
    syncThreadLabel();
    setUiState('idle');
    $messageInput.value = '';
    onInput();
    $messages.innerHTML = '';
    $messages.appendChild(createWelcomeMessage());
    $messageInput.focus();
}

// ── Format bot text ───────────────────────────────────────────
function formatBotText(text) {
    if (typeof marked !== 'undefined' && marked.parse) {
        const html = marked.parse(text, { breaks: true, gfm: true });
        return sanitizeMarkdownHtml(html);
    }
    // fallback when marked CDN is unavailable
    const escaped = escHtml(text);
    return escaped
        .replace(/\n/g, '<br>')
        .replace(/(\d+)\.\s+([^<]+)/g, '<span class="numbered-item">$1. $2</span>');
}

function sanitizeMarkdownHtml(html) {
    return html
        .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')
        .replace(/<iframe\b[^<]*(?:(?!<\/iframe>)<[^<]*)*<\/iframe>/gi, '')
        .replace(/\son\w+\s*=\s*"[^"]*"/gi, '')
        .replace(/\son\w+\s*=\s*'[^']*'/gi, '');
}

function escHtml(str) {
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function escAttr(str) {
    return escHtml(String(str || '')).replace(/'/g, '&#39;');
}

function scrollBottom() {
    $messages.scrollTop = $messages.scrollHeight;
}
