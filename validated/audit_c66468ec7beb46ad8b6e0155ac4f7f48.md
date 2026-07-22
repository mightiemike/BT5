### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Non-Allowlisted Addresses to Bypass the Deposit Gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the LP position `owner` against the allowlist but silently ignores the `sender` (the actual token provider). Because `addLiquidity` accepts an arbitrary `owner` address and pulls tokens from `msg.sender` via callback, any non-allowlisted address can bypass the deposit gate by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct actors to the `beforeAddLiquidity` hook:

- `sender` = `msg.sender` — the address that calls `addLiquidity` and pays tokens through the `metricOmmModifyLiquidityCallback`
- `owner` — an arbitrary address supplied by the caller that receives the LP position [1](#0-0) 

The `DepositAllowlistExtension` receives both but discards `sender` (the first, unnamed parameter) and only checks `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

The pool's own test suite confirms that `sender` and `owner` are fully decoupled — Alice (sender/payer) can deposit tokens on behalf of Bob (owner), with Alice's balance decreasing and Bob receiving the LP shares: [3](#0-2) 

The `MetricOmmPoolLiquidityAdder` explicitly supports this pattern via `addLiquidityExactShares(pool, owner, ...)` where `owner` is a free parameter and `msg.sender` is always the payer: [4](#0-3) 

**Attack path:**

1. Pool admin deploys a permissioned pool with `DepositAllowlistExtension` and allowlists only `alice`.
2. Non-allowlisted `attacker` calls `pool.addLiquidity(owner=alice, salt, deltas, callbackData, extensionData)` directly (or via the `LiquidityAdder`).
3. `beforeAddLiquidity(sender=attacker, owner=alice, ...)` is called. The check evaluates `allowedDepositor[pool][alice]` → `true`. The hook returns the valid selector.
4. The pool proceeds; `attacker` pays tokens through the callback; `alice` receives the LP position.
5. The deposit allowlist is fully bypassed — `attacker`'s tokens are now inside the permissioned pool.

---

### Impact Explanation

The deposit allowlist is the primary access-control mechanism for permissioned/KYC pools. Bypassing it allows any address to inject liquidity into a pool that is supposed to be restricted. Consequences include:

- Non-KYC/non-allowlisted capital entering a regulated pool, breaking compliance invariants.
- An attacker can manipulate bin balances and per-bin value-per-share metrics (relevant to `OracleValueStopLossExtension` watermarks) without being an authorized participant.
- LP positions are credited to the allowlisted `owner`, but the pool's token reserves are funded by an unauthorized party — the pool's accounting of "who deposited" is permanently corrupted.

**Severity: High** — the core invariant of the extension (only allowlisted depositors can add liquidity) is completely broken with no privilege required.

---

### Likelihood Explanation

**Likelihood: Medium.** The attack requires no special privilege — any EOA or contract can call `addLiquidity` with an arbitrary `owner`. The only cost to the attacker is the tokens deposited (which go to the allowlisted `owner`'s LP position). In adversarial scenarios (e.g., griefing a regulated pool, manipulating stop-loss watermarks), this cost is acceptable. The `MetricOmmPoolLiquidityAdder` makes the pattern trivially accessible via `addLiquidityExactShares(pool, allowlisted_address, ...)`.

---

### Recommendation

Replace the `owner` check with a `sender` check in `beforeAddLiquidity`. The `sender` is the actual token provider and the entity whose access should be gated:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

Pool admins who intentionally want to gate by position owner (not token provider) should document this explicitly and use a separate extension.

---

### Proof of Concept

```solidity
// Setup: pool with DepositAllowlistExtension; only alice is allowlisted
depositAllowlist.setAllowedToDeposit(pool, alice, true);

// Attacker (not allowlisted) deposits on behalf of alice
// Tokens come from attacker; LP position goes to alice
vm.prank(attacker);
pool.addLiquidity(
    alice,          // owner = allowlisted → check passes
    salt,
    deltas,
    callbackData,   // attacker pays tokens here
    extensionData
);

// Result: attacker's tokens are in the pool; allowlist was never enforced on the actual payer
assertGt(pool.positionShares(alice, salt, binIdx), 0); // alice has shares
// attacker's balance decreased — non-allowlisted capital is now inside the permissioned pool
```

The `beforeAddLiquidity` hook receives `sender=attacker` as its first argument but never reads it, so the revert path `NotAllowedToDeposit` is never reached. [5](#0-4)

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

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L240-254)
```text
  function test_exactShares_usesMsgSenderAsPayerNotOwner() public {
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);
    address bob = makeAddr("bob");

    uint256 aliceWethBefore = weth.balanceOf(alice);
    uint256 bobWethBefore = weth.balanceOf(bob);

    vm.prank(alice);
    helper.addLiquidityExactShares(address(pool), bob, 12, d, type(uint256).max, type(uint256).max, "");

    uint256 bobShares = stateView.positionBinShares(address(pool), bob, 12, int8(4));
    assertGt(bobShares, 0);
    assertLt(weth.balanceOf(alice), aliceWethBefore);
    assertEq(weth.balanceOf(bob), bobWethBefore);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
