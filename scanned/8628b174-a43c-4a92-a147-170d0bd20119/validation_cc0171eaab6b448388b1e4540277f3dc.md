### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Allowlist Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is intended to gate `addLiquidity` by depositor address. However, its `beforeAddLiquidity` hook silently discards the `sender` parameter (the actual token-providing caller) and only checks `owner` (the LP-position recipient). Because `addLiquidity` accepts an arbitrary `owner` that need not equal `msg.sender`, an address that is not on the allowlist can deposit tokens into a restricted pool by naming an authorized address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct actors to the extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol  L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

- `sender` = `msg.sender` — the address that calls `addLiquidity` and **provides tokens** via the swap callback.
- `owner` — the address that **receives the LP position**; supplied as a parameter and may differ from `msg.sender`.

`DepositAllowlistExtension.beforeAddLiquidity` receives both but ignores `sender` entirely:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  L32-42
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

The first parameter (`sender`) is unnamed and never read. The guard evaluates `allowedDepositor[pool][owner]`, not `allowedDepositor[pool][sender]`. Because there is no `msg.sender == owner` constraint in `addLiquidity` (unlike `removeLiquidity`, which enforces `msg.sender == owner`), any caller can supply an arbitrary `owner`.

This is structurally inconsistent with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swap initiator) and ignores `recipient`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Impact Explanation

A pool operator deploys a pool with `DepositAllowlistExtension` to enforce that only KYC-approved or otherwise authorized addresses can supply liquidity. Because the guard checks `owner` rather than `sender`, an unauthorized address can:

1. Call `pool.addLiquidity(authorizedOwner, ...)` where `authorizedOwner` is any address already on the allowlist (e.g., a second wallet the attacker controls, or any address they can name).
2. The allowlist check passes (`allowedDepositor[pool][authorizedOwner] == true`).
3. The unauthorized `sender` provides the tokens via the `IMetricOmmModifyLiquidityCallback` callback.
4. The LP position is credited to `authorizedOwner`.

If the attacker controls `authorizedOwner` (e.g., it is their own second address that was previously allowlisted), they have fully bypassed the deposit restriction: their unauthorized capital enters the pool and they retain economic control of the resulting LP position. The allowlist invariant — that only approved addresses can deposit — is broken.

---

### Likelihood Explanation

- Any pool that deploys `DepositAllowlistExtension` with a non-trivial allowlist is affected.
- The attacker needs only one address on the allowlist (e.g., their own previously approved address, or any address they can name as `owner`).
- No special permissions, flash loans, or oracle manipulation are required — a single direct call to `addLiquidity` suffices.
- The bypass is reachable by any unprivileged external caller.

---

### Recommendation

Replace the ignored first parameter with a named `sender` and gate on it, consistent with `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intended semantics are to restrict LP-position ownership (not token provision), the NatSpec and the mapping name (`allowedDepositor`) must be updated to reflect that, and the bypass risk documented explicitly.

---

### Proof of Concept

```
Setup:
  pool P configured with DepositAllowlistExtension E
  E.allowedDepositor[P][alice] = true   // alice is authorized
  E.allowedDepositor[P][bob]  = false   // bob is NOT authorized
  bob controls alice (e.g., alice is bob's second EOA)

Attack:
  bob calls P.addLiquidity(alice, salt, deltas, callbackData, extensionData)
    → pool calls E.beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)
    → E checks allowedDepositor[P][alice] == true  ✓  (bob's address never checked)
    → guard passes
    → pool executes addLiquidity, calls bob's callback to collect tokens
    → LP shares credited to alice
    → bob (who controls alice) now holds LP position funded by unauthorized capital
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
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
  }
```
