### Title
`DepositAllowlistExtension` checks position `owner` instead of actual depositor `sender`, allowing any non-allowlisted address to bypass the deposit gate — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the address that actually pays tokens and triggers the callback) and instead checks the `owner` argument (the position recipient). Because `addLiquidity` explicitly supports an operator pattern where `msg.sender ≠ owner`, any non-allowlisted address can bypass the deposit gate by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct actors to the extension hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

Here `msg.sender` is the actual depositor who will be called back to pay tokens, and `owner` is the address that receives the position (explicitly allowed to differ — the operator pattern). The extension hook signature is `beforeAddLiquidity(address sender, address owner, ...)`.

`DepositAllowlistExtension.beforeAddLiquidity` drops `sender` entirely (first parameter is unnamed) and checks `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [2](#0-1) 

The contract's own NatSpec states it "Gates `addLiquidity` by **depositor** address, per pool," and the storage mapping is named `allowedDepositor`. Yet the check is performed against `owner`, not the depositor.

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly reads and checks `sender`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

The two sibling extensions are structurally identical except that the deposit one binds the wrong parameter.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism to restrict who may add liquidity (e.g., KYC/compliance gates, private pools). Because the check is on `owner` rather than `sender`:

1. **Full allowlist bypass**: Any non-allowlisted address Eve can call `pool.addLiquidity(allowlisted_alice, salt, deltas, ...)`. The extension sees `allowedDepositor[pool][alice] == true` and passes. Eve pays the tokens via the modify-liquidity callback; Alice receives the position.
2. **Unsolicited position griefing**: Eve can force-deposit into Alice's position key without Alice's consent, potentially locking Alice's position in a state she did not choose.
3. **Protocol-level access control failure**: Any pool that relies on `DepositAllowlistExtension` to enforce a closed or permissioned LP set has no effective gate on the actual payer.

This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality" impact categories.

---

### Likelihood Explanation

- Requires no special privilege — any EOA or contract can call `addLiquidity` with an arbitrary `owner`.
- The operator pattern (`msg.sender ≠ owner`) is a documented, first-class feature of `addLiquidity`.
- The `MetricOmmPoolLiquidityAdder` periphery contract explicitly exercises this pattern (`addLiquidityExactShares(pool, owner, ...)` where `owner` is caller-supplied). [4](#0-3) 

Likelihood is **High** — the bypass is trivially reachable by any address with no preconditions.

---

### Recommendation

**Short term:** Bind `sender` (the actual depositor) instead of `owner` in `beforeAddLiquidity`:

```solidity
// BEFORE (wrong actor)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) { ... }
}

// AFTER (correct actor)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) { ... }
}
```

**Long term:** Add a unit test that calls `addLiquidity` with `msg.sender ≠ owner` against a pool configured with `DepositAllowlistExtension` and asserts that the non-allowlisted sender is rejected even when `owner` is allowlisted.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with DepositAllowlistExtension
  pool admin calls setAllowedToDeposit(pool, alice, true)
  Eve is NOT in the allowlist

Attack:
  Eve calls pool.addLiquidity(
      owner = alice,   // allowlisted — passes the broken check
      salt  = 0,
      deltas = <valid bins>,
      callbackData = ...,
      extensionData = ""
  )

Extension check (beforeAddLiquidity):
  sender = Eve   ← discarded (unnamed param)
  owner  = Alice ← checked: allowedDepositor[pool][alice] == true → PASS

Result:
  Eve's tokens are pulled via metricOmmModifyLiquidityCallback
  Alice's position is credited with Eve's deposit
  Eve has successfully deposited into a pool she is not allowlisted for
```

The root cause is at `DepositAllowlistExtension.sol:32` — the first parameter (the actual depositor `sender`) is unnamed and never read, while `owner` at line 38 is checked instead. [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-60)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
```
