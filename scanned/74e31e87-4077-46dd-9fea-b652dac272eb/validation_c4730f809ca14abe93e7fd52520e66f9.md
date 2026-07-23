### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original User, Allowing Allowlist Bypass or Breaking Allowlisted Users' Router Access — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

When a swap is routed through `MetricOmmSimpleRouter`, the `SwapAllowlistExtension` receives the **router's address** as `sender` instead of the original user's address. This creates a binary failure: either the router is not allowlisted (breaking the standard swap path for all allowlisted users) or the router is allowlisted (allowing any non-allowlisted user to bypass the guard entirely by routing through the router).

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards this value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` gates access by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*()`, the router calls `pool.swap()`. At that point `msg.sender` inside `pool.swap()` is the **router contract**, so `sender` delivered to the extension is the router's address — not the original user's address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin configures the allowlist per-user via `setAllowedToSwap`: [4](#0-3) 

There is no mechanism in the extension or the router to substitute the original `msg.sender` of the router call. The `extensionData` bytes are forwarded opaquely and `SwapAllowlistExtension` does not parse them.

This produces two mutually exclusive broken states:

- **Router not allowlisted**: Every allowlisted user who calls through `MetricOmmSimpleRouter` is rejected by the guard. The standard periphery swap path is completely broken for curated pools.
- **Router allowlisted**: Any non-allowlisted user can bypass the guard by routing through `MetricOmmSimpleRouter`, rendering the allowlist ineffective.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, protocol-owned addresses, or whitelisted market makers) cannot simultaneously allow those users to use the standard router and exclude everyone else. Either the router path is broken for legitimate users, or the allowlist is fully bypassed by any public user. This is a broken core pool functionality with direct policy-bypass consequences and potential fund-impacting outcomes on pools where the allowlist is a financial control (e.g., restricting who can drain liquidity via swaps).

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects users to interact via `MetricOmmSimpleRouter` (the documented standard periphery path) will immediately encounter this. The trigger requires no special privileges: any public user can call the router. The pool admin's only recourse is to require all users to call the pool directly, bypassing the supported periphery entirely.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the **original user** rather than the intermediate router. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change in both the router and the extension.

2. **Check `sender` only when it is not a trusted router, and check the callback-context user otherwise**: The pool could expose the original initiator via transient storage (already used for reentrancy guards), and the extension reads it.

The simplest correct fix mirrors the external report's pattern: the entity that the guard is meant to control must be the entity whose address is actually checked. The extension must receive and verify the **end-user address**, not the address of any intermediary contract on the call stack.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension as a before-swap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true).
   Alice is the only allowlisted swapper.
3. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...).
   - Router calls pool.swap(recipient, ...).
   - pool.swap() passes msg.sender = router to _beforeSwap.
   - SwapAllowlistExtension checks allowedSwapper[pool][router].
   - If router is allowlisted: Bob's swap succeeds — allowlist bypassed.
   - If router is not allowlisted: Bob's swap reverts correctly.
4. Alice (allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...).
   - Same path: extension checks allowedSwapper[pool][router], not allowedSwapper[pool][alice].
   - Alice's swap reverts even though she is explicitly allowlisted.
   => Either the allowlist is bypassable (router allowlisted) or the standard swap path
      is broken for legitimate users (router not allowlisted).
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-20)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
