### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the actual user, allowing any user to bypass the per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` intermediates the swap, `sender` is the **router address**, not the actual end-user. If the pool admin allowlists the router (the only way to enable router-based swaps on a restricted pool), every user — including those explicitly excluded from the allowlist — can bypass the guard by routing through the router.

---

### Finding Description

`MetricOmmPool` passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the direct caller of `pool.swap()`) is in the per-pool allowlist: [2](#0-1) 

When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the **router contract**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. [3](#0-2) 

The pool admin faces an impossible choice:

- **Do not allowlist the router** → router-based swaps always revert; the router is unusable with this pool.
- **Allowlist the router** → `allowedSwapper[pool][router] = true` satisfies the check for every user who routes through the router, regardless of whether that user is individually permitted.

The extension's own naming (`allowedSwapper`, `setAllowedToSwap(address pool_, address swapper, ...)`) and NatSpec ("Gates `swap` by swapper address, per pool") make clear the intent is to gate **individual users**, not intermediary contracts. [4](#0-3) 

---

### Impact Explanation

Any user excluded from the allowlist can execute swaps in a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point) instead of calling `pool.swap()` directly. The pool settles the swap and transfers output tokens to the user's chosen `recipient`. The allowlist guard — the sole access-control mechanism for swap restriction — is completely neutralised. This breaks the core pool invariant that only permitted addresses may swap, and constitutes an admin-boundary break via an unprivileged path.

---

### Likelihood Explanation

The trigger is a routine, non-malicious admin action: allowlisting the router so that normal users can interact with the pool through the standard periphery. Any pool that (a) configures `SwapAllowlistExtension` and (b) allowlists the router is immediately vulnerable. No special privileges, flash loans, or oracle manipulation are required — a plain call to the router suffices.

---

### Recommendation

The extension must verify the **ultimate user**, not the direct caller. Two complementary fixes:

1. **Pass the real user through the router.** Have `MetricOmmSimpleRouter` supply the original `msg.sender` as an explicit `sender` parameter to `pool.swap()`, and have the pool forward that value (rather than its own `msg.sender`) to extension hooks. This requires a pool-level change to accept a trusted `sender` from whitelisted routers.

2. **Check `recipient` instead of `sender` in the extension.** If the pool's design cannot be changed, `SwapAllowlistExtension` can gate on the `recipient` argument (the address that receives output tokens), which is always the end-user even when a router intermediates. This is a simpler, extension-only fix but changes the semantic from "who initiates" to "who receives."

Either way, the extension's NatSpec and setter names should be updated to reflect which address is actually being checked.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][userA] = true   // only userA is permitted
  allowedSwapper[pool][router] = true  // admin allowlists router for normal operation

Attack (userB, not in allowlist):
  1. userB calls MetricOmmSimpleRouter.exactInputSingle(
       tokenIn, tokenOut, pool, amountIn, minOut, recipient=userB, ...
     )
  2. Router calls pool.swap(recipient=userB, ...)
     → pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes; userB receives output tokens

Result: userB bypasses the allowlist entirely.

Direct call (same userB, no router):
  1. userB calls pool.swap(recipient=userB, ...)
     → pool's msg.sender = userB
  2. SwapAllowlistExtension checks allowedSwapper[pool][userB] → false → revert ✓
``` [2](#0-1) [1](#0-0)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L280-295)
```text
    uint256 packedSlot0Final = Slot0Library.loadPackedSlot0();
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
```
