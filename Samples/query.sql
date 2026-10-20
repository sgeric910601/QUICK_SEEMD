-- 每月營收
SELECT strftime('%Y-%m', created_at) AS month,
       COUNT(*) AS orders, SUM(total) AS revenue
FROM orders o
LEFT JOIN customers c ON c.id = o.customer_id
WHERE status = 'paid' AND total > 100
GROUP BY month ORDER BY month DESC LIMIT 12;
