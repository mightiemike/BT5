### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently discards the `sender` argument and instead validates the `owner` (LP position recipient). Because `MetricOmmPool.addLiquidity` imposes no constraint that `msg.sender == owner`, any unauthorized caller can bypass the allowlist by supplying an already-authorized owner address.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address arguments: `sender` (the actual `msg.sender` of `addLiquidity`) and `owner` (the LP position recipient). The first argument is unnamed and discarded; the allowlist lookup is performed on `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly names and checks `sender`:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

`MetricOmmPool.addLiquidity` places no restriction on the relationship between `msg.sender` and `owner`:

```solidity
// MetricOmmPool.sol L182-196
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    ...
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [3](#0-2) 

The hook is dispatched with `sender = msg.sender` and `owner = caller-supplied address`. Because the extension ignores `sender` and only validates `owner`, any caller can pass an authorized owner address and the guard approves the call unconditionally.

The `isAllowedToDeposit` view function further confirms the intended semantics — it accepts a `depositor` parameter — but the hook checks the wrong field:

```solidity
// DepositAllowlistExtension.sol L28-29
function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
}
``` [4](#0-3) 

---

### Impact Explanation

The deposit allowlist guard is completely inoperative. Any address — regardless of allowlist status — can add liquidity to a pool that the admin intended to restrict. The unauthorized caller pays the input tokens; the specified authorized `owner` receives the LP shares. Consequences include:

- **Allowlist invariant broken**: The pool admin's access control is entirely bypassed; the restricted pool accepts deposits from any address.
- **Forced LP positions**: An attacker can mint LP shares into any authorized owner's account without their consent, potentially griefing them with unwanted exposure.
- **Compliance/permissioning failure**: Pools deployed with this extension for KYC, whitelist, or institutional-access purposes provide no actual restriction.

---

### Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with a known authorized owner address (which is public on-chain from prior deposits or admin configuration events). No special permissions, flash loans, or complex setup are needed. Any unauthorized party can trigger this immediately.

---

### Recommendation

Name and check `sender` instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
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

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured in `BEFORE_ADD_LIQUIDITY_ORDER`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is authorized.
3. Unauthorized `attacker` calls:
   ```solidity
   pool.addLiquidity(alice, salt, deltas, callbackData, extensionData);
   ```
4. Pool dispatches `_beforeAddLiquidity(attacker, alice, ...)`.
5. `DepositAllowlistExtension.beforeAddLiquidity` evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `attacker` successfully deposits; `alice` receives LP shares; the allowlist is bypassed.
7. `attacker` can repeat with any authorized owner address, injecting liquidity into the restricted pool at will.

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L28-29)
```text
  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
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
