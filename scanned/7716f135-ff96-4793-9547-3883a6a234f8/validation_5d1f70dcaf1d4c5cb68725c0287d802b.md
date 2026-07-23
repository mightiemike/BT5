### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end-user. If the pool admin allowlists the router (the natural action to enable router-mediated swaps), every unpermissioned user can bypass the allowlist by routing through the router. The wrong identity is gated — the intermediary instead of the economically relevant actor — mirroring the external report's unit-mismatch pattern.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument the pool passes — which is `msg.sender` of the `pool.swap()` call itself:

```solidity
_beforeSwap(
  msg.sender,   // ← this becomes `sender` in the extension
  recipient,
  ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or any router entry point), the router calls `pool.swap()`. At that point `msg.sender` of `pool.swap()` is the **router**, not the end-user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The pool admin faces an impossible choice:

| Admin action | Result |
|---|---|
| Allowlist the router | Every user can bypass the allowlist by routing through the router |
| Do not allowlist the router | Allowlisted users cannot use the router; they must call the pool directly |

Neither option preserves the intended invariant: "only specific users may swap on this pool."

The `DepositAllowlistExtension` does not share this flaw because it gates `owner` (the position recipient), which the pool passes directly from the caller's argument and is not substituted by the router. [3](#0-2) 

The swap allowlist has no equivalent protection.

---

### Impact Explanation

**Broken core pool functionality / admin-boundary break.** A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., a private OTC pool, a KYC-gated venue, or a pool that must only trade with a specific market maker) can be fully bypassed by any unpermissioned user routing through `MetricOmmSimpleRouter`. The allowlist guard is rendered inoperative on the public router path, which is the primary user-facing entry point. Any swap executed by an unpermissioned user drains pool liquidity at oracle prices, directly impacting LP principal and fee accrual.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the standard periphery entry point documented and used in tests. A pool admin enabling a swap allowlist would naturally also allowlist the router to avoid breaking the standard UX for permitted users. The bypass requires no special privileges, no flash loans, and no exotic token behavior — any EOA can call the router. [4](#0-3) 

---

### Recommendation

Pass the end-user identity through the call chain so the extension can gate the correct actor. Two approaches:

1. **Encode the real user in `extensionData`**: The router encodes `msg.sender` (the end-user) into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires the extension to trust the router's encoding, which reintroduces a trust assumption.

2. **Preferred — check `sender` only when the caller is the pool directly; require routers to pass the originating user as a verified field**: Add a `trustedForwarder` registry to the extension so that when `sender` is a known router, the extension reads the real user from a standardized `extensionData` slot. Untrusted callers are checked as-is.

3. **Simplest — document that the allowlist only works for direct pool calls and remove the router from the permitted path**: Gate the router itself out of the allowlist and require allowlisted users to call the pool directly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension, extensionOrders.beforeSwap = extension1
  - Pool admin calls setAllowedToSwap(pool, router, true)   // natural: "allow the router"
  - Pool admin does NOT allowlist attacker (0xAttacker)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=attacker, ...)
     → pool.swap() msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true  ✓
  5. Swap executes; attacker receives token output from a restricted pool

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist bypassed
``` [5](#0-4) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
```
