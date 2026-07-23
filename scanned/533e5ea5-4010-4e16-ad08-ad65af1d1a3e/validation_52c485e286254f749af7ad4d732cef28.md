### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual caller of `addLiquidity`) and instead validates `owner` (the LP position owner, a freely-chosen parameter). Because `owner` is caller-controlled, any address can bypass the allowlist by naming an already-allowlisted address as `owner`.

---

### Finding Description

The pool calls `_beforeAddLiquidity(msg.sender, owner, ...)` where `sender = msg.sender` is the actual depositor and `owner` is the LP position owner supplied by the caller. [1](#0-0) 

The extension interface exposes both: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and therefore **never read**. The guard only checks `owner`: [3](#0-2) 

The allowlist is keyed as `allowedDepositor[pool][depositor]` and is described as gating "by depositor address": [4](#0-3) 

Because `owner` is a free parameter in `pool.addLiquidity(owner, salt, deltas, ...)`, any caller can pass an allowlisted address as `owner`, satisfy the guard, and complete the deposit. The actual depositor (`sender`) is never validated.

Note: `SwapAllowlistExtension.beforeSwap` does **not** share this bug — it correctly reads the `sender` parameter: [5](#0-4) 

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity (e.g., KYC/whitelist-gated pools). With this bug the guard is completely ineffective:

- Any unauthorized address can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)` directly (bypassing the `MetricOmmPoolLiquidityAdder`).
- The extension approves the call because `allowedDepositor[pool][allowlistedAddress]` is `true`.
- The unauthorized caller pays the tokens via the `metricOmmModifyLiquidityCallback`; the allowlisted address receives the LP shares.
- The unauthorized caller can also set `owner = self` if they are allowlisted, but the critical path is the bypass: a non-allowlisted address deposits by nominating any allowlisted address as `owner`.

Consequences: unauthorized dilution of existing LP positions, manipulation of bin composition in restricted pools, and complete nullification of the pool admin's access-control intent. This is broken core pool functionality with direct LP-asset impact.

---

### Likelihood Explanation

- The pool's `addLiquidity` function is `external` with no caller restriction beyond the extension itself.
- The bypass requires only a single direct call to the pool with a known allowlisted address as `owner` — no special privileges, no flash loans, no complex setup.
- Any actor who can observe the allowlist (public mapping) can execute the bypass immediately.

---

### Recommendation

Replace the unnamed first parameter with `sender` and validate it instead of `owner`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
-   if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+   if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
``` [3](#0-2) 

---

### Proof of Concept

```solidity
// Setup: pool deployed with DepositAllowlistExtension; Alice is allowlisted, Bob is not.
extension.setAllowedToDeposit(pool, alice, true);
// allowedDepositor[pool][alice] = true
// allowedDepositor[pool][bob]   = false (default)

// Bob calls the pool directly, naming Alice as owner.
// The extension checks allowedDepositor[pool][alice] → true → no revert.
// Bob's tokens are pulled in the callback; Alice receives the LP shares.
vm.prank(bob);
pool.addLiquidity(
    alice,          // owner — allowlisted, so guard passes
    salt,
    deltas,
    callbackData,   // pulls tokens from bob via metricOmmModifyLiquidityCallback
    extensionData
);

// Result: Bob (not allowlisted) has successfully deposited into the restricted pool.
// Alice holds the LP shares; the allowlist provided zero protection.
```

### Citations

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-21)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);

```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-14)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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
