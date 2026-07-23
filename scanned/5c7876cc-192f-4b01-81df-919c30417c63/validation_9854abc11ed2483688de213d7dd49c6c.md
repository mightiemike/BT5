### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Allowlist Guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual caller of `addLiquidity` who provides tokens via callback) and instead gates on `owner` (the LP-share recipient). An attacker who controls any allowlisted address can deposit from an entirely unauthorized address, fully bypassing the restriction the extension is designed to enforce.

---

### Finding Description

In `MetricOmmPool.addLiquidity`, the pool passes two distinct addresses to the extension hook:

- `sender` = `msg.sender` of `addLiquidity` — the entity that will be called back to provide tokens
- `owner` = caller-supplied argument — the address that receives the minted LP shares [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but discards it (unnamed `address`), then checks only `owner`: [3](#0-2) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller of `swap`): [4](#0-3) 

The inconsistency is structural: the deposit guard checks the wrong actor.

---

### Impact Explanation

**Allowlist bypass (unauthorized deposit):** An attacker who controls two addresses — `attackerEOA` (not allowlisted) and `attackerContract` (allowlisted) — executes:

1. `attackerEOA` calls `pool.addLiquidity(attackerContract, salt, deltas, callbackData, extensionData)`.
2. Extension evaluates `allowedDepositor[pool][attackerContract]` → `true` → hook passes.
3. Pool calls back `attackerEOA` (the real `msg.sender`) to pull tokens.
4. LP shares are minted to `attackerContract`.
5. `attackerContract` calls `removeLiquidity` to recover the tokens.

The attacker has injected liquidity into a restricted pool from an unauthorized address, defeating the entire purpose of the allowlist. Because `removeLiquidity` enforces `msg.sender == owner`, the attacker retains full control of the position through `attackerContract`.

**False block (router pattern broken):** If a pool admin allowlists a router contract as the authorized depositor, but users are the `owner`, every router-mediated deposit reverts because `allowedDepositor[pool][user]` is `false` even though the router (the actual token provider) is allowlisted. This renders the pool unusable for any router-based liquidity flow.

---

### Likelihood Explanation

High. The bypass requires only that the attacker controls one allowlisted address — a trivially achievable condition for any participant who was ever granted allowlist access. No special oracle conditions, price manipulation, or privileged roles are needed. The false-block scenario triggers automatically whenever a router is used, which is the standard periphery pattern in this protocol.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
// Before (wrong actor):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}

// After (correct actor):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][attackerContract] = true
  allowedDepositor[pool][attackerEOA]      = false   // attacker's real depositing address

Attack:
  // Step 1: attackerEOA calls addLiquidity with owner = attackerContract
  pool.addLiquidity(
      attackerContract,   // owner  ← allowlisted, passes the guard
      salt,
      deltas,
      callbackData,
      extensionData
  );
  // msg.sender = attackerEOA (not allowlisted, but never checked)

  // Step 2: Extension checks allowedDepositor[pool][attackerContract] → true → no revert

  // Step 3: Pool calls attackerEOA.metricOmmSwapCallback(...) to pull tokens
  //         attackerEOA transfers tokens to pool

  // Step 4: LP shares minted to attackerContract

  // Step 5: attackerContract calls removeLiquidity to recover tokens
  pool.removeLiquidity(attackerContract, salt, deltas, extensionData);
  // msg.sender == owner → passes NotPositionOwner check

Result: attackerEOA (unauthorized) successfully deposited into a restricted pool.
``` [3](#0-2) [5](#0-4) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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
