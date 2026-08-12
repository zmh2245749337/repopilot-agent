# Optional field causes 500

创建订单时省略备注字段会触发 500，请定位对 `None` 的不安全字符串操作并补充测试。

