### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Router Address Checked Instead of Actual Swapper - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the **router address**, not the actual end-user. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the gate to every unprivileged user who calls the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap`. When the user goes through `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`), the router is the direct caller of `pool.swap`: [4](#0-3) 

So the allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The pool admin must allowlist the router address for any router-mediated swap to succeed. Once the router is allowlisted, **every user** — including those explicitly excluded from the per-user allowlist — can swap freely by calling the public router.

---

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's primary mechanism for restricting swap access to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers). A complete bypass of this guard means:

- Unauthorized users can execute swaps on pools that were intended to be private or restricted.
- LP funds in restricted pools are exposed to the full public, defeating the pool admin's access-control intent.
- Any oracle-driven price movement that the pool admin expected only allowlisted parties to act on can be exploited by any user via the router.

This constitutes a broken core guard with direct fund-exposure consequences for LP assets in restricted pools.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it.
- A pool admin who wants to support both router-mediated swaps (for allowlisted users) and the allowlist guard is forced to allowlist the router, which immediately opens the gate to all users.
- There is no mechanism in the router to forward the original `msg.sender` to the pool; the router is always the direct caller.
- The existing unit tests for `SwapAllowlistExtension` only test direct pool calls (`vm.prank(address(pool))`), not router-mediated calls, so this path is untested. [5](#0-4) 

---

### Recommendation

The extension must gate on the **end-user identity**, not the direct caller of `pool.swap`. Two complementary fixes:

1. **Pass the original user through the router.** Add a `payer`/`originator` field to the swap call or encode it in `extensionData` so the extension can verify the true initiator. The router already tracks the original `msg.sender` in transient storage as the payer; expose it in a standardized way.

2. **Check `sender` only when `sender` is not a known router.** Alternatively, `SwapAllowlistExtension` can maintain a separate `trustedRouter` mapping and, when `sender` is a trusted router, extract the real user from `extensionData` (which the router would populate with `abi.encode(msg.sender)`).

Until fixed, pools that rely on `SwapAllowlistExtension` for access control should not allowlist the router address, accepting that router-mediated swaps are unavailable for those pools.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  5. Swap executes successfully for the non-allowlisted attacker.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
```

The root cause is at `SwapAllowlistExtension.sol:37` where `sender` (the router) is checked instead of the actual end-user, and at `MetricOmmPool.sol:231` where `msg.sender` (the router) is forwarded as `sender` with no mechanism to carry the original user's identity through the router hop. [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
