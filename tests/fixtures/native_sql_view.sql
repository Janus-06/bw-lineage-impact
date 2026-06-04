SELECT
  s.sales_id,
  c.customer_name,
  SUM(s.net_amount) AS total_amount,
  CASE WHEN s.net_amount > 1000 THEN 'HIGH' ELSE 'NORMAL' END AS amount_band
FROM zsales_fact s
JOIN zcustomer_dim c ON s.customer_id = c.customer_id
WHERE s.calday >= '20240101'
GROUP BY s.sales_id, c.customer_name, s.net_amount
