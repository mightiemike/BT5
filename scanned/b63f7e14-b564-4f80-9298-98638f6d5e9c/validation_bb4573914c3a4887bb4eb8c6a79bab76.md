The call chain is fully traceable. Here is the analysis:

**Call chain:**
1. User → `MetricOmmSimpleRouter.exactInputSingle` (`msg.sender` = user)
2. Router → `MetricOmmPool.swap(...)` (`msg.sender` at pool = **router address**)
3. Pool → `_beforeSwap(msg.sender=router, ...)` [1](#0-0) 
4. `ExtensionCalling._beforeSwap` encodes `sender=router` and calls the extension [2](#0-1) 
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]` [3](#0-2) 

The hook checks whether the **router** is allowlisted, not the original EOA. This is a real identity mismatch.

---

### Title
SwapAllowlistExtension Checks Router Identity Instead of Original Caller, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the immediate caller is the router contract, not the original user. If the router is allowlisted for a pool (which is necessary for any router-mediated swap to work on that pool), every unprivileged user can bypass the allowlist by routing through the router.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // <-- immediate caller, not original EOA
  recipient,
  ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension: [4](#0-3) 

`SwapAllowlistExtension.beforeSwap` then checks:
```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [5](#0-4) 

Here `msg.sender` = pool (correct), and `sender` = whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle` or any `exact*` function, the pool's `msg.sender` is the **router contract address**:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
``` [6](#0-5) 

This creates a binary failure mode:

| Router allowlisted? | Result |
|---|---|
| Yes | **Any user** can bypass the allowlist by routing through the router |
| No | **Allowlisted users** cannot use the router at all |

There is no way to configure the extension to correctly gate individual users who arrive via the router.

### Impact Explanation
The `SwapAllowlistExtension` is the designated mechanism for restricting which addresses may swap on a given pool. A pool admin who deploys a restricted pool (e.g., a private institutional pool, or a pool that uses the allowlist as a stop-loss gate) and allowlists the router to support normal UX inadvertently opens the gate to all users. Any unprivileged attacker can call `exactInputSingle` or `exactOutputSingle` on the router targeting the restricted pool and the `beforeSwap` hook will pass because it sees the allowlisted router address as `sender`, not the blocked user. This breaks the core access-control invariant the extension was designed to enforce and constitutes broken core pool functionality.

### Likelihood Explanation
The router is a first-party periphery contract. Any pool that wants to support router-mediated swaps must allowlist it. The bypass requires only a single public transaction through the router — no privileged access, no special setup, no flash loan. Any user who knows the pool uses `SwapAllowlistExtension` can exploit this immediately.

### Recommendation
The `sender` identity passed through the hook chain must represent the **original initiating account**, not the immediate `msg.sender`. Two complementary fixes:

1. **In the router**: pass the original `msg.sender` as `extensionData` or via a dedicated field so the extension can recover it. Alternatively, use a transient-storage slot (already used for callback context) to record the original caller and expose it to extensions.
2. **In `SwapAllowlistExtension`**: document that `sender` is the immediate pool caller and warn that router-mediated flows will present the router address. Until the core passes the original initiator, the extension cannot safely gate individual users who arrive via the router.

The cleanest fix is to have `MetricOmmPool.swap` accept an explicit `originator` parameter (or read it from transient storage set by the router) and pass that as `sender` to the extension chain instead of `msg.sender`.

### Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only allowed swapper
  allowedSwapper[pool][router] = true         // router allowlisted so alice can use it
  allowedSwapper[pool][bob] = false           // bob is blocked

Attack (single transaction, no privileges):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    -> router calls pool.swap(...)
       msg.sender at pool = router
    -> pool calls _beforeSwap(sender=router, ...)
    -> SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] == true
    -> PASSES — bob's swap executes on the restricted pool

Direct call (control):
  bob calls pool.swap(...) directly
    -> SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][bob] == false
    -> REVERTS NotAllowedToSwap — correctly blocked
```

The allowlist blocks direct calls from `bob` but passes router-mediated calls from `bob` because the hook sees the router's address, not `bob`'s. The invariant "only allowlisted addresses may swap" is violated for every pool that allowlists the router.

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
