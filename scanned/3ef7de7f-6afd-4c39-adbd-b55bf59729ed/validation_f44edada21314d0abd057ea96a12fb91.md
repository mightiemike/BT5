### Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on `owner` instead of `sender`, allowing un-allowlisted callers to bypass the deposit guard - (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is documented and configured to gate `addLiquidity` **by depositor address**. However, its `beforeAddLiquidity` hook checks the `owner` parameter (the position owner) against the allowlist instead of the `sender` parameter (the actual caller/depositor). Any un-allowlisted address can bypass the guard entirely by specifying an allowlisted address as the position owner.

---

### Finding Description

The contract's NatSpec and admin API are unambiguous about intent:

- The contract comment reads: *"Gates `addLiquidity` by depositor address, per pool."*
- The admin setter is `setAllowedToDeposit(address pool_, address depositor, bool allowed)` — the parameter is named `depositor`.
- The storage mapping is `allowedDepositor[pool][depositor]`. [1](#0-0) 

Despite this, the enforcement hook checks `owner` — the second positional argument — not `sender` (the first, unnamed argument, which is the actual caller): [2](#0-1) 

The pool passes both `sender` (the `msg.sender` of the `addLiquidity` call) and `owner` (the address that will own the resulting position) as distinct arguments to the extension: [3](#0-2) 

Because `owner` is a caller-controlled parameter that can be set to any address, an un-allowlisted `sender` can pass any allowlisted address as `owner` and the guard will approve the call. The `SwapAllowlistExtension` — the sibling extension — correctly checks `sender` in its analogous hook, confirming the `DepositAllowlistExtension` check is inconsistent and erroneous: [4](#0-3) 

---

### Impact Explanation

The pool admin's deposit access-control boundary is bypassed by an unprivileged path. Any address — regardless of allowlist status — can deposit into a pool that the admin intended to restrict. This breaks the admin-boundary invariant: the pool admin configures an allowlist expecting only approved depositors to add liquidity, but the check is applied to the wrong actor. Pools using this extension for KYC/compliance gating or LP-tier restrictions are fully open to unauthorized deposits.

---

### Likelihood Explanation

Medium. The bypass requires no special privilege — only knowledge of one allowlisted address (which may be publicly observable on-chain via `AllowedToDepositSet` events) and the ability to call `addLiquidity` directly or through the router. No flash loan, oracle manipulation, or privileged role is needed.

---

### Recommendation

Replace the `owner` check with `sender` in `beforeAddLiquidity`:

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
```

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured in `BEFORE_ADD_LIQUIDITY_ORDER`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is allowlisted.
3. Bob (not allowlisted) calls `pool.addLiquidity(owner=alice, salt=..., deltas=..., data=...)` with Bob's tokens.
4. The pool calls `extension.beforeAddLiquidity(sender=bob, owner=alice, ...)`.
5. The extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. Bob's deposit succeeds. The allowlist is fully bypassed; Bob's tokens enter the pool with Alice as the position owner. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
