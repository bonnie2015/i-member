package com.mock.scrm;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import javax.annotation.PostConstruct;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@SpringBootApplication
@RestController
public class MockScrmApplication {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Path STATE_FILE = Paths.get("/app/data/state.json");
    private static final Path DEFAULT_SEED_DIR = Paths.get("/app/seed");
    private static final Path LOCAL_SEED_DIR = Paths.get("data/seed");
    private static final String USER_ID = "api_ticket_probe";
    private static final List<String> PROFILE_FIELDS = Arrays.asList(
            "basic_info", "value_segment", "preferences", "behavior_summary", "social"
    );

    private Map<String, Object> state;

    public static void main(String[] args) {
        SpringApplication app = new SpringApplication(MockScrmApplication.class);
        app.setDefaultProperties(map("server.port", "3658"));
        app.run(args);
    }

    @PostConstruct
    public synchronized void load() throws IOException {
        if (Files.exists(STATE_FILE)) {
            state = JSON.readValue(STATE_FILE.toFile(), new TypeReference<Map<String, Object>>() {});
        } else {
            state = seedState();
            save();
        }
    }

    @GetMapping("/health")
    public Map<String, Object> health() {
        return map("status", "ok", "state_file", STATE_FILE.toString());
    }

    @GetMapping("/api/scrm/user_profile")
    public ResponseEntity<Map<String, Object>> getUserProfile(
            @RequestParam(value = "user_id", required = false) String userId,
            @RequestParam(value = "fields", required = false) String fields
    ) {
        String normalizedUserId = text(userId);
        if (normalizedUserId.isEmpty()) {
            return scrmError(HttpStatus.BAD_REQUEST, "Missing required parameter: user_id");
        }
        Map<String, Object> profileRecord = mapAt(mapAt(state, "user_profiles"), normalizedUserId);
        if (profileRecord.isEmpty()) {
            return scrmError(HttpStatus.NOT_FOUND, "User not found");
        }
        Map<String, Object> filtered = filterProfile(mapAt(profileRecord, "profile"), fields);
        if (filtered == null) {
            return scrmError(HttpStatus.BAD_REQUEST, "invalid fields: " + fields);
        }
        return ResponseEntity.ok(scrmSuccess(map(
                "user_id", profileRecord.get("user_id"),
                "profile", filtered,
                "last_update", profileRecord.get("last_update")
        )));
    }

    @GetMapping("/order")
    public Map<String, Object> listOrders(
            @RequestParam(value = "status", required = false) String status,
            @RequestParam(value = "keyword", required = false) String keyword,
            @RequestParam(value = "start_time", required = false) String startTime,
            @RequestParam(value = "end_time", required = false) String endTime,
            @RequestParam(value = "page", defaultValue = "1") int page,
            @RequestParam(value = "page_size", defaultValue = "20") int pageSize
    ) {
        String normalizedKeyword = text(keyword).toLowerCase();
        List<Map<String, Object>> filtered = new ArrayList<>();
        for (Map<String, Object> order : listAt(state, "orders")) {
            if (!text(status).isEmpty() && !text(order.get("status")).equals(status)) {
                continue;
            }
            if (!normalizedKeyword.isEmpty()
                    && !text(order.get("order_id")).toLowerCase().contains(normalizedKeyword)
                    && !text(order.get("items_summary")).toLowerCase().contains(normalizedKeyword)) {
                continue;
            }
            if (!withinRange(text(order.get("created_at")), startTime, endTime)) {
                continue;
            }
            filtered.add(orderSummary(order));
        }
        filtered.sort(Comparator.comparing(item -> text(item.get("created_at")), Comparator.reverseOrder()));
        Map<String, Object> result = paginate(filtered, page, pageSize);
        result.put("orders", result.remove("items"));
        return ok(result);
    }

    @GetMapping("/order/{orderId}")
    public ResponseEntity<Map<String, Object>> getOrder(@PathVariable String orderId) {
        Map<String, Object> order = findById(listAt(state, "orders"), "order_id", orderId);
        if (order == null) {
            return error(HttpStatus.NOT_FOUND, "order not found");
        }
        return ResponseEntity.ok(ok(order));
    }

    @GetMapping("/user/level")
    public Map<String, Object> getUserLevel() {
        return ok(mapAt(state, "user_level"));
    }

    @GetMapping("/user/score")
    public Map<String, Object> getUserScore(
            @RequestParam(value = "page", defaultValue = "1") int page,
            @RequestParam(value = "page_size", defaultValue = "20") int pageSize,
            @RequestParam(value = "start_time", required = false) String startTime,
            @RequestParam(value = "end_time", required = false) String endTime
    ) {
        Map<String, Object> score = mapAt(state, "score");
        List<Map<String, Object>> records = new ArrayList<>();
        for (Map<String, Object> record : listAt(score, "records")) {
            if (withinRange(text(record.get("time")), startTime, endTime)) {
                records.add(record);
            }
        }
        records.sort(Comparator.comparing(item -> text(item.get("time")), Comparator.reverseOrder()));
        Map<String, Object> result = paginate(records, page, pageSize);
        result.put("user_id", USER_ID);
        result.put("score_balance", score.get("score_balance"));
        result.put("records", result.remove("items"));
        return ok(result);
    }

    @GetMapping("/ticket")
    public Map<String, Object> listTickets(
            @RequestParam(value = "ticket_type", required = false) String ticketType,
            @RequestParam(value = "source_channel", required = false) String sourceChannel,
            @RequestParam(value = "status", required = false) String status,
            @RequestParam(value = "keyword", required = false) String keyword,
            @RequestParam(value = "start_time", required = false) String startTime,
            @RequestParam(value = "end_time", required = false) String endTime,
            @RequestParam(value = "page", defaultValue = "1") int page,
            @RequestParam(value = "page_size", defaultValue = "20") int pageSize
    ) {
        String normalizedKeyword = text(keyword).toLowerCase();
        List<Map<String, Object>> filtered = new ArrayList<>();
        for (Map<String, Object> ticket : listAt(state, "tickets")) {
            if (!text(ticketType).isEmpty() && !text(ticket.get("ticket_type")).equals(ticketType)) continue;
            if (!text(sourceChannel).isEmpty() && !text(ticket.get("source_channel")).equals(sourceChannel)) continue;
            if (!text(status).isEmpty() && !text(ticket.get("status")).equals(status)) continue;
            if (!normalizedKeyword.isEmpty()
                    && !text(ticket.get("ticket_id")).toLowerCase().contains(normalizedKeyword)
                    && !text(ticket.get("title")).toLowerCase().contains(normalizedKeyword)) {
                continue;
            }
            if (!withinRange(text(ticket.get("created_at")), startTime, endTime)) {
                continue;
            }
            filtered.add(ticketSummary(ticket));
        }
        filtered.sort(Comparator.comparing(item -> text(item.get("created_at")), Comparator.reverseOrder()));
        Map<String, Object> result = paginate(filtered, page, pageSize);
        result.put("tickets", result.remove("items"));
        return ok(result);
    }

    @GetMapping("/ticket/{ticketId}")
    public ResponseEntity<Map<String, Object>> getTicket(@PathVariable String ticketId) {
        Map<String, Object> ticket = findById(listAt(state, "tickets"), "ticket_id", ticketId);
        if (ticket == null) {
            return error(HttpStatus.NOT_FOUND, "ticket not found");
        }
        return ResponseEntity.ok(ok(ticket));
    }

    @PostMapping("/ticket")
    public synchronized Map<String, Object> createTicket(@RequestBody Map<String, Object> payload) throws IOException {
        String ticketType = text(payload.get("ticket_type"));
        String title = text(payload.get("title"));
        String content = text(payload.get("content"));
        if (ticketType.isEmpty() || title.isEmpty() || content.isEmpty()) {
            throw new MockHttpException(HttpStatus.BAD_REQUEST, "ticket_type, title, content are required");
        }

        String now = OffsetDateTime.now().toString();
        Map<String, Object> ticket = map(
                "ticket_id", nextTicketId(),
                "ticket_type", ticketType,
                "status", "open",
                "status_label", "待处理",
                "title", title,
                "content", content,
                "description", payload.get("description"),
                "images", payload.get("images") instanceof List ? payload.get("images") : new ArrayList<>(),
                "order_id", payload.get("order_id"),
                "order_item_id", payload.get("order_item_id"),
                "sku_id", payload.get("sku_id"),
                "quantity", payload.get("quantity"),
                "source_channel", payload.get("source_channel"),
                "latest_progress", "工单已创建，等待处理",
                "expected_finish_time", "2026-05-05T18:00:00+08:00",
                "created_at", now,
                "timeline", list(map("time", now, "action", "工单创建", "operator", "mock_scrm"))
        );
        listAt(state, "tickets").add(ticket);
        save();
        return ok(ticket);
    }

    @GetMapping("/mock/admin/state")
    public synchronized Map<String, Object> adminState() {
        return map("state_file", STATE_FILE.toString(), "state", state);
    }

    @PostMapping("/mock/admin/reset")
    public synchronized Map<String, Object> reset() throws IOException {
        state = seedState();
        save();
        return adminState();
    }

    @GetMapping(value = "/mock/admin", produces = MediaType.TEXT_HTML_VALUE)
    public String adminPage() {
        return ADMIN_HTML;
    }

    private synchronized void save() throws IOException {
        Files.createDirectories(STATE_FILE.getParent());
        Path temp = STATE_FILE.resolveSibling("state.tmp");
        JSON.writerWithDefaultPrettyPrinter().writeValue(temp.toFile(), state);
        Files.move(temp, STATE_FILE, StandardCopyOption.REPLACE_EXISTING);
    }

    private String nextTicketId() {
        return "TK" + OffsetDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd"))
                + String.format("%04d", listAt(state, "tickets").size() + 1);
    }

    private static Map<String, Object> seedState() throws IOException {
        Path seedDir = seedDir();
        Map<String, Object> loyalty = readSeedObject(seedDir.resolve("loyalty.json"));
        return map(
                "user_profiles", readSeedObject(seedDir.resolve("user_profiles.json")),
                "orders", readSeedList(seedDir.resolve("orders.json")),
                "tickets", readSeedList(seedDir.resolve("tickets.json")),
                "score", mapAt(loyalty, "score"),
                "user_level", mapAt(loyalty, "user_level")
        );
    }

    private static Path seedDir() {
        String configured = text(System.getenv("MOCK_SCRM_SEED_DIR"));
        if (!configured.isEmpty()) return Paths.get(configured);
        return Files.exists(DEFAULT_SEED_DIR) ? DEFAULT_SEED_DIR : LOCAL_SEED_DIR;
    }

    private static Map<String, Object> readSeedObject(Path path) throws IOException {
        return JSON.readValue(path.toFile(), new TypeReference<Map<String, Object>>() {});
    }

    private static List<Map<String, Object>> readSeedList(Path path) throws IOException {
        return JSON.readValue(path.toFile(), new TypeReference<List<Map<String, Object>>>() {});
    }

    private static Map<String, Object> orderSummary(Map<String, Object> order) {
        return map("order_id", order.get("order_id"), "status", order.get("status"), "status_label", order.get("status_label"), "amount", order.get("amount"), "items_summary", order.get("items_summary"), "items", order.get("items"), "source_channel", order.get("source_channel"), "created_at", order.get("created_at"));
    }

    private static Map<String, Object> ticketSummary(Map<String, Object> ticket) {
        return map("ticket_id", ticket.get("ticket_id"), "ticket_type", ticket.get("ticket_type"), "status", ticket.get("status"), "status_label", ticket.get("status_label"), "title", ticket.get("title"), "source_channel", ticket.get("source_channel"), "created_at", ticket.get("created_at"));
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> mapAt(Map<String, Object> source, String key) {
        Object value = source.get(key);
        return value instanceof Map ? (Map<String, Object>) value : new LinkedHashMap<>();
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> listAt(Map<String, Object> source, String key) {
        Object value = source.get(key);
        return value instanceof List ? (List<Map<String, Object>>) value : new ArrayList<>();
    }

    private static Map<String, Object> findById(List<Map<String, Object>> items, String field, String value) {
        for (Map<String, Object> item : items) {
            if (text(item.get(field)).equals(value)) return item;
        }
        return null;
    }

    private static Map<String, Object> filterProfile(Map<String, Object> profile, String fields) {
        if (text(fields).isEmpty()) return profile;
        Map<String, Object> filtered = new LinkedHashMap<>();
        for (String part : fields.split(",")) {
            String field = part.trim();
            if (!PROFILE_FIELDS.contains(field)) return null;
            if (profile.containsKey(field)) filtered.put(field, profile.get(field));
        }
        return filtered;
    }

    private static boolean withinRange(String value, String start, String end) {
        if (text(start).isEmpty() && text(end).isEmpty()) return true;
        if (text(value).isEmpty()) return false;
        try {
            OffsetDateTime time = OffsetDateTime.parse(value);
            if (!text(start).isEmpty() && time.isBefore(OffsetDateTime.parse(start))) return false;
            if (!text(end).isEmpty() && time.isAfter(OffsetDateTime.parse(end))) return false;
            return true;
        } catch (DateTimeParseException e) {
            return false;
        }
    }

    private static Map<String, Object> paginate(List<Map<String, Object>> items, int page, int pageSize) {
        int safePage = Math.max(page, 1);
        int safePageSize = Math.max(Math.min(pageSize, 100), 1);
        int start = Math.min((safePage - 1) * safePageSize, items.size());
        int end = Math.min(start + safePageSize, items.size());
        return map("total", items.size(), "page", safePage, "page_size", safePageSize, "has_more", end < items.size(), "items", new ArrayList<>(items.subList(start, end)));
    }

    private static ResponseEntity<Map<String, Object>> error(HttpStatus status, String message) {
        return ResponseEntity.status(status).body(map("detail", message));
    }

    private static ResponseEntity<Map<String, Object>> scrmError(HttpStatus status, String message) {
        return ResponseEntity.status(status).body(map("code", status.value(), "message", message, "data", null));
    }

    private static Map<String, Object> ok(Map<String, Object> data) {
        return map("code", 0, "message", "ok", "data", data);
    }

    private static Map<String, Object> scrmSuccess(Map<String, Object> data) {
        return map("code", 0, "message", "success", "data", data);
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static Map<String, Object> map(Object... items) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (int i = 0; i + 1 < items.length; i += 2) {
            result.put(String.valueOf(items[i]), items[i + 1]);
        }
        return result;
    }

    private static List<Object> list(Object... items) {
        return new ArrayList<>(Arrays.asList(items));
    }

    private static class MockHttpException extends RuntimeException {
        private final HttpStatus status;
        MockHttpException(HttpStatus status, String message) {
            super(message);
            this.status = status;
        }
    }

    @ExceptionHandler(MockHttpException.class)
    public ResponseEntity<Map<String, Object>> handle(MockHttpException exception) {
        return error(exception.status, exception.getMessage());
    }

    private static final String ADMIN_HTML = "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"/><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/><title>Mock SCRM</title><style>body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f6f7f9;color:#1f2933}header{padding:18px 24px;background:#111827;color:white;display:flex;justify-content:space-between;align-items:center}main{padding:20px 24px;display:grid;gap:18px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}section{background:white;border:1px solid #e5e7eb;border-radius:8px;padding:14px}h2{font-size:16px;margin:0 0 10px}.item{border-top:1px solid #eef0f3;padding:10px 0}.item:first-of-type{border-top:0}.muted{color:#667085;font-size:12px}code{background:#eef2f7;padding:2px 5px;border-radius:4px}button{border:0;background:#2563eb;color:white;border-radius:6px;padding:8px 10px;cursor:pointer}pre{white-space:pre-wrap;word-break:break-word;background:#0f172a;color:#d1e7ff;padding:10px;border-radius:6px;max-height:320px;overflow:auto}</style></head><body><header><div>Mock SCRM 数据台 <span class=\"muted\" id=\"file\"></span></div><div><button onclick=\"loadState()\">刷新</button> <button onclick=\"resetState()\">重置数据</button></div></header><main><section><h2>订单</h2><div id=\"orders\"></div></section><section><h2>工单</h2><div id=\"tickets\"></div></section><section style=\"grid-column:1/-1\"><h2>原始状态</h2><pre id=\"raw\"></pre></section></main><script>const esc=v=>String(v??'').replace(/[&<>\\\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\\\"':'&quot;',\"'\":'&#39;'}[c]));function render(id,items,fn){document.getElementById(id).innerHTML=items?.length?items.map(fn).join(''):'<div class=\"muted\">暂无数据</div>'}async function loadState(){const data=await fetch('/mock/admin/state').then(r=>r.json());const s=data.state;document.getElementById('file').textContent=data.state_file;render('orders',s.orders||[],o=>`<div class=\"item\"><b>${esc(o.order_id)}</b><div>${esc(o.items_summary)} - ${esc(o.status_label)}</div><div class=\"muted\">${(o.items||[]).map(i=>esc(i.name)+' #'+esc(i.product_id)+' / color '+esc(i.color_id)).join('<br>')}</div></div>`);render('tickets',s.tickets||[],t=>`<div class=\"item\"><b>${esc(t.ticket_id)}</b> <code>${esc(t.status_label)}</code><div>${esc(t.title)}</div><div class=\"muted\">${esc(t.ticket_type)} / ${esc(t.created_at)}</div></div>`);document.getElementById('raw').textContent=JSON.stringify(s,null,2)}async function resetState(){await fetch('/mock/admin/reset',{method:'POST'});await loadState()}loadState()</script></body></html>";
}
