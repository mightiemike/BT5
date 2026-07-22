### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router**, not the end user. If the pool admin allowlists the router (the natural setup for any pool that supports periphery access), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → MetricOmmPool.swap(recipient, ...)   [msg.sender = router]
     → _beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first parameter — i.e., the router address, not the originating user: [3](#0-2) 

The allowlist is keyed `pool → swapper → bool`: [4](#0-3) 

**The invariant break:** A pool admin who wants to restrict swaps to a curated set of addresses must also allowlist the router for users to use the supported periphery. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every user who routes through it, regardless of whether that user is in the curated set. The allowlist is completely bypassed for all router-mediated swaps.

---

### Impact Explanation

Any unprivileged user can trade on a pool that the admin intended to restrict to a specific allowlist, simply by calling `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant). The pool's curation boundary — the only mechanism preventing unauthorized parties from extracting value from or interacting with a curated pool — is silently voided. This constitutes a direct admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses the pool admin's configured access control, matching the "allowlist bypass" and "wrong-actor binding" impact categories.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the canonical, documented periphery entry point for swaps.
- Any pool admin who wants users to use the router must allowlist it, which is the expected operational setup.
- No special knowledge or privileged access is required; any user can call `exactInputSingle` on the router.
- The bypass is silent — the extension returns success without any indication that the end user was not individually authorized.

---

### Recommendation

The extension must gate the **economically relevant actor** — the originating user — not the immediate caller of the pool. Two approaches:

1. **Pass the original user through the router:** Have `MetricOmmSimpleRouter` pass the user's address as `extensionData`, and have the extension decode and check it. This requires a protocol-level convention.
2. **Check `recipient` instead of `sender`:** For swap allowlists, the recipient is often the user; however, this is also spoofable.
3. **Preferred — use a transient-storage forwarding pattern:** The router writes the originating `msg.sender` into transient storage before calling the pool, and the extension reads it from there. This is consistent with the protocol's existing use of EIP-1153 transient storage. [3](#0-2) 

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so legitimate users can use the periphery.
extension.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT individually allowlisted.
// Alice calls the router directly — the extension sees sender=router, passes.
vm.prank(alice);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1_000,
        amountOutMinimum: 0,
        recipient: alice,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Alice's swap succeeds despite not being in the allowlist.
// The extension checked allowedSwapper[pool][router] == true, not allowedSwapper[pool][alice].
```

The root cause is confirmed at: [5](#0-4) 

and the actor mismatch originates at: [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
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
