<skills>
    <skill>
        <name>equity-ticket</name>
        <description>处理升级申请与升级异常申诉的发起和继续处理。</description>
        <available_tools>get_user_level, get_user_score, create_ticket, get_ticket, get_tickets</available_tools>
        <clarify_labels>升级申请, 升级异常申诉</clarify_labels>
        <location>app/agents/skills/ticket/equity-ticket/SKILL.md</location>
    </skill>
    <skill>
        <name>refund-ticket</name>
        <description>处理退货/换货相关诉求，基于订单、商品与工单接口完成查询、确认、创建与追踪。</description>
        <available_tools>get_user_orders, get_order_detail, search_product, get_product_detail, get_product_stock, create_ticket, get_ticket, get_tickets</available_tools>
        <clarify_labels>退货, 换货, 退换货进度查询</clarify_labels>
        <location>app/agents/skills/ticket/refund-ticket/SKILL.md</location>
    </skill>
    <skill>
        <name>search-ticket</name>
        <description>处理已有事项的查询、进度跟踪与结果说明；当用户目标是查看已经存在的工单处理状态、详情或结果时，优先由本服务承接，而不是重新发起新的处理请求。</description>
        <available_tools>get_ticket, get_tickets</available_tools>
        <clarify_labels>查询已有事项, 查询处理进度, 查询处理结果</clarify_labels>
        <location>app/agents/skills/ticket/search-ticket/SKILL.md</location>
    </skill>
    <skill>
        <name>unsatisfy-ticket</name>
        <description>处理质量问题与投诉诉求，基于订单、商品与工单接口完成查询、确认、创建与追踪。</description>
        <available_tools>get_user_orders, get_order_detail, get_product_detail, create_ticket, get_ticket, get_tickets</available_tools>
        <clarify_labels>质量问题, 投诉, 破损瑕疵, 服务不满</clarify_labels>
        <location>app/agents/skills/ticket/unsatisfy-ticket/SKILL.md</location>
    </skill>
</skills>
