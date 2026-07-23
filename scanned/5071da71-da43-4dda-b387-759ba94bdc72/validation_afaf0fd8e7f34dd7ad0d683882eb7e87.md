Based on my investigation of the codebase, I can identify a concrete analog to the Wildcat H-01 bug class — a wrong-actor binding in the swap allowlist guard that allows the allowlist to be bypassed through the router.

---

### Title
SwapAllowlistExtension gates the router's address instead of the end user, allowing full allowlist bypass through MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **end user** is allowlisted. If the router is allowlisted (which is required for any router-mediated swap to succeed on a curated pool), every non-allowlisted user can bypass the curation policy by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows: [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the extension's caller), and `sender` is the first argument forwarded by the pool. The pool's `swap` function sets `sender = msg.sender` of the `swap` call, as confirmed by the NatSpec: [2](#0-1) 

> "Swap allowlist rejected `msg.sender`."

The pool then forwards this value into `ExtensionCalling._beforeSwap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exact*`, the router calls `pool.swap(...)` directly. The pool's `msg.sender` is the router, so `sender = router`. The extension evaluates:

```
allowedSwapper[pool][router]
```

instead of:

```
allowedSwapper[pool][end_user]
```

For the curated pool to be usable via the router at all, the pool admin must allowlist the router. Once the router is allowlisted, **any** address — including addresses the admin explicitly excluded — can bypass the allowlist by calling through the router.

Contrast this with `DepositAllowlistExtension`, which correctly gates `owner` (an explicit parameter the caller supplies), not `sender`: [4](#0-3) 

The deposit extension is not affected because `owner` is explicitly passed and the liquidity adder passes the actual user as `owner`. The swap extension has no equivalent explicit-user parameter — it relies solely on `sender`, which collapses to the router's address on every router-mediated swap.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a known set of counterparties (e.g., KYC'd addresses or institutional partners). The admin must also allowlist `MetricOmmSimpleRouter` so that those counterparties can use the standard router UX. Once the router is allowlisted, any unpermissioned address can call `MetricOmmSimpleRouter.exact*` and the extension will pass because `allowedSwapper[pool][router] == true`. The curated pool's entire access-control invariant is broken: non-allowlisted users can execute swaps, draining LP liquidity at oracle prices without the pool admin's consent.

**Severity: High** — direct loss of LP assets and complete curation-policy failure on every pool that uses `SwapAllowlistExtension` together with the router.

---

### Likelihood Explanation

The scenario requires no privileged action beyond the pool admin's own necessary setup step (allowlisting the router). Any pool that:
1. Uses `SwapAllowlistExtension` as a `beforeSwap` hook, and
2. Allowlists `MetricOmmSimpleRouter` so that permitted users can trade via the standard UX

is immediately exploitable by any address. This is the expected production configuration for a curated pool with a router-facing UI.

---

### Recommendation

The extension must gate the **economic actor**, not the immediate caller. Two complementary fixes:

1. **Pass the end user explicitly**: Modify the pool's `swap` function to accept an explicit `sender` parameter (similar to how `addLiquidity` accepts an explicit `owner`), and have the router forward `msg.sender` (the end user) as that argument. The extension then checks the user, not the router.

2. **Alternatively, check `recipient` or require the router to embed the user in `extensionData`**: The extension can decode the real user from `extensionData` when the immediate caller is a known router, but this requires a trusted-router registry and is more complex.

The cleanest fix mirrors the deposit path: make `sender` an explicit, caller-supplied parameter on `swap` so the router can pass the actual end user's address, and the extension always gates the economically relevant actor.

---

### Proof of Concept

```solidity
// Pool admin setup (legitimate):
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);   // router must be allowed
swapAllowlist.setAllowedToSwap(address(pool), alice, true);              // alice is a permitted trader
// bob is NOT added to the allowlist

// Attack (bob bypasses the allowlist):
// bob calls router directly — pool.msg.sender == router, extension checks allowedSwapper[pool][router] == true
router.exactInput(
    ExactInputParams({
        pool: address(pool),
        recipient: bob,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        extensionData: ""
    })
);
// swap succeeds; bob receives tokens despite never being allowlisted
// LP assets are transferred to a non-permitted counterparty
```

The `beforeSwap` hook receives `sender = address(router)`, finds `allowedSwapper[pool][router] == true`, and passes. Bob's swap executes at the oracle price, extracting LP value without authorization.

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
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
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
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
