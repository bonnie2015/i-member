<skills>
    <skill>
        <name>refund-ticket</name>
        <description>处理退货/退款申请。</description>
        <available_tools>get_user_orders, get_order_detail, get_product_detail, create_ticket, search_products, read_file</available_tools>
        <location>app/app/skills/ticket/refund-ticket/SKILL.md</location>
    </skill>
    <skill>
        <name>search-ticket</name>
        <description>处理已有事项的查询、进度跟踪与结果说明；当用户目标是查看已经存在的工单处理状态、详情或结果时，优先由本服务承接，而不是重新发起新的处理请求。</description>
        <available_tools>get_ticket, get_tickets</available_tools>
        <location>app/app/skills/ticket/search-ticket/SKILL.md</location>
    </skill>
    <skill>
        <name>unsatisfy-ticket</name>
        <description>处理用户不满或投诉，包括质量问题、破损瑕疵、服务不满等场景。</description>
        <available_tools>get_user_orders, get_order_detail, get_product_detail, create_ticket</available_tools>
        <location>app/app/skills/ticket/unsatisfy-ticket/SKILL.md</location>
    </skill>
</skills>
