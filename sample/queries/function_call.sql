SELECT NVL(bonus, 0) AS bonus_safe
FROM hr.compensation
WHERE payout_date > SYSDATE - INTERVAL '7' DAY;
