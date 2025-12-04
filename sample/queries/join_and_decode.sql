SELECT
    e.employee_id,
    e.first_name,
    e.last_name,
    DECODE(e.status, 'A', 'ACTIVE', 'I', 'INACTIVE', 'UNKNOWN') AS status_text,
    d.department_name
FROM hr.employees e
JOIN hr.departments d ON e.department_id = d.department_id
WHERE e.hire_date >= TO_DATE('2020-01-01', 'YYYY-MM-DD');
