The vulnerability is real and traceable through the exact code. Here is the full analysis:

---

### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unprivileged address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` argument (the actual caller of `pool.addLiquidity`) and instead gates on `owner` (the position recipient). Because `pool.addLiquidity` accepts an arbitrary `owner` with no restriction on who the caller is, any non-allowlisted address can bypass the allowlist by specifying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`, which forwards both to the extension: [1](#0-0) 

The extension receives `sender` as its first parameter but silently discards it (unnamed `address,`). The allowlist check is performed only on `owner`: [2](#0-1) 

So the guard evaluates `allowedDepositor[pool][owner]`, not `allowedDepositor[pool][sender]`. An attacker (address B) who is not on the allowlist can call `pool.addLiquidity(owner=A, ...)` where A is an allowlisted address. The extension sees `owner=A`, finds it allowed, and passes. B pays the tokens (via the modify-liquidity callback), A receives the position.

The pool itself imposes no restriction on who may call `addLiquidity` with an arbitrary `owner`: [3](#0-2) 

The periphery `MetricOmmPoolLiquidityAdder` even documents and tests this "add on behalf of another owner" pattern explicitly: [4](#0-3) 

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole on-chain mechanism for restricting who may add liquidity to a pool. With this bug, the restriction is completely ineffective: any address can deposit into a "restricted" pool by naming an allowlisted address as `owner`. The allowlist invariant — that only approved addresses can add liquidity — is broken. Concrete consequences:

- Non-allowlisted addresses inject liquidity into pools intended to be restricted (e.g., KYC-gated, institutional, or protocol-controlled pools).
- Attacker can grief allowlisted LPs by front-running their deposits, forcing unfavorable bin compositions before the legitimate LP deposits.
- Pool admin's access control intent is entirely defeated without any privileged action.

---

### Likelihood Explanation

The bypass requires only a public call to `pool.addLiquidity` with `owner` set to any allowlisted address. No privileged access, no special token behavior, no oracle manipulation. Any address that can observe the allowlist state (public mapping) and call the pool can exploit this. Likelihood is high.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor) rather than `owner` (the position recipient):

```solidity
// current (broken)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// fixed
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [2](#0-1) 

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_allowlistBypass() public {
    address allowlistedLP = makeAddr("allowlistedLP");
    address attacker     = makeAddr("attacker");

    // Pool admin allowlists only allowlistedLP
    vm.prank(admin);
    depositExtension.setAllowedToDeposit(address(pool), allowlistedLP, true);

    // Fund attacker and approve pool
    deal(address(token0), attacker, 1e18);
    deal(address(token1), attacker, 1e18);
    vm.startPrank(attacker);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);

    // Attacker calls addLiquidity with owner = allowlistedLP
    // Extension checks allowedDepositor[pool][allowlistedLP] == true → passes
    LiquidityDelta memory delta = ...; // valid delta
    pool.addLiquidity(allowlistedLP, 0, delta, callbackData, "");
    vm.stopPrank();

    // Attacker (non-allowlisted) successfully added liquidity to a restricted pool
    // allowlistedLP now has a position they did not request
}
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L211-220)
```text
  function test_exactShares_canAddOnBehalfOfAnotherOwner() public {
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);
    address bob = makeAddr("bob");

    vm.prank(alice);
    helper.addLiquidityExactShares(address(pool), bob, 1, d, type(uint256).max, type(uint256).max, "");

    uint256 bobShares = stateView.positionBinShares(address(pool), bob, 1, int8(4));
    assertGt(bobShares, 0);
  }
```
