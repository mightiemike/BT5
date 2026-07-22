### Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension::beforeSwap`, Breaking Allowlist Identity Invariant — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `MetricOmmPool::swap`. When a user routes through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router`, so the hook checks whether the **router** is allowlisted — not the actual user. This breaks the allowlist in both directions: allowlisted users cannot swap through the router, and allowlisting the router to fix that opens the gate to every user.

The "paused pool" framing in the question is a red herring: `swap` carries `whenNotPaused`, so a paused pool reverts before `_beforeSwap` is ever reached. The real vulnerability is the identity mismatch on any live pool using this extension.

---

### Finding Description

**Call chain — direct swap (correct):**

```
User → pool.swap(...)
         msg.sender = user
         _beforeSwap(msg.sender=user, ...)
         extension.beforeSwap(sender=user, ...)
         allowedSwapper[pool][user]  ✓
```

**Call chain — router swap (broken):**

```
User → router.exactInputSingle(...)
         router → pool.swap(...)
                    msg.sender = router
                    _beforeSwap(msg.sender=router, ...)
                    extension.beforeSwap(sender=router, ...)
                    allowedSwapper[pool][router]  ✗ (user not checked)
```

In `MetricOmmPool::swap`, the first argument to `_beforeSwap` is always `msg.sender`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards that value unchanged as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap` — the router, not the user: [3](#0-2) 

The router never forwards the original user identity; it simply calls `pool.swap` as itself: [4](#0-3) 

---

### Impact Explanation

Two concrete broken outcomes on any live allowlisted pool:

1. **Allowlisted users are silently blocked from the router.** A pool admin allowlists `userA`. `userA` calls `exactInputSingle`; the hook sees `sender = router`, which is not in the allowlist, and reverts with `NotAllowedToSwap`. The user cannot use the protocol's own router despite being explicitly permitted.

2. **Allowlisting the router defeats the per-user gate entirely.** If the admin adds the router to the allowlist to restore router access, every address on the network can swap through the router, because the hook no longer distinguishes between users. The allowlist becomes meaningless.

Both outcomes constitute broken core functionality: the allowlist extension is the mechanism pool designers use to restrict swap access, and it fails for the primary public entry point (`MetricOmmSimpleRouter`).

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` with `allowAllSwappers = false` and expects users to interact via the router is immediately affected. The router is the standard periphery entry point; most non-technical users will use it. No special attacker capability is required — a normal `exactInputSingle` call is sufficient to trigger either failure mode.

---

### Recommendation

The extension must verify the **originating user**, not the immediate pool caller. Two viable approaches:

- **Pass `tx.origin` as an additional parameter** in the hook signature (requires core change; introduces its own risks with smart-contract wallets).
- **Require callers to embed the real user address in `extensionData`** and have the extension verify it against a signature or a trusted forwarder registry.
- **Allowlist at the router level**: the router checks allowlist membership before calling the pool, and the extension trusts only the router (but this still requires the extension to know the canonical router address).

The cleanest fix is to redesign the hook signature to carry a verified `originSender` that the pool populates from a trusted source (e.g., a callback-verified payer address already stored in transient storage by the router).

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, allowAllSwappers = false
// Admin allowlists userA directly: allowedSwapper[pool][userA] = true

// Direct swap — succeeds
vm.prank(userA);
pool.swap(recipient, true, 1e18, 0, "", "");

// Router swap — reverts NotAllowedToSwap even though userA is allowlisted
ExactInputSingleParams memory p = ExactInputSingleParams({
    pool: address(pool),
    recipient: recipient,
    tokenIn: token0,
    amountIn: 1e18,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
});
vm.prank(userA);
router.exactInputSingle(p); // ← reverts: allowedSwapper[pool][router] == false

// Fix attempt: admin allowlists the router
// allowedSwapper[pool][router] = true
// Now userB (not allowlisted) can bypass the gate:
vm.prank(userB);
router.exactInputSingle(p); // ← succeeds: allowlist bypassed
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
