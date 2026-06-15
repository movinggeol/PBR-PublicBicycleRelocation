import pulp

# 1. 문제 정의
prob = pulp.LpProblem('Bike_Rebalancing', pulp.LpMinimize)

# 2. 변수 정의
x = pulp.LpVariable('x', lowBound=0, cat='Integer')
y = pulp.LpVariable('y', lowBound=0, cat='Integer')

# 3. 목적함수 정의
prob += 2 * x - 3 * y

# 4. 제약조건 추가
prob += x >= 5
prob += y <= 10

# 5. 최적화 수행
status = prob.solve(pulp.PULP_CBC_CMD(msg=0))


# 6. 결과 출력
print("Status : ", pulp.LpStatus[prob.status])
print("OPT =", pulp.value(prob.objective))
print("x = ", pulp.value(x))
print("y = ", pulp.value(y))