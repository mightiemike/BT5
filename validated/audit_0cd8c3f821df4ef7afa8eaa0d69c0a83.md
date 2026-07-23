### Title
`SwapAllowlistExtension.beforeSwap` binds the allowlist check to the router address rather than the end user, allowing any caller to bypass the swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate which addresses may swap on a curated pool. Its `beforeSwap` hook checks the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps for allowed users), every user — including disallowed ones — can bypass the allowlist by routing through the router.

---

### Finding Description

**Root cause in `SwapAllowlistExtension.beforeSwap`:** [1](#0-0) 

The hook receives `sender` as its first argument and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the caller of the extension) and `sender` is whatever the pool forwarded.

**How the pool populates `sender`:** [2](#0-1) 

The pool passes its own `msg.sender` — the immediate caller of `pool.swap()` — as `sender`. When a user goes through the router, `msg.sender` to the pool is the router contract, so `sender = router`.

**How `ExtensionCalling._beforeSwap` encodes the call:** [3](#0-2) 

There is no mechanism to thread the original end-user identity through the hook call. The extension only ever sees the immediate pool caller.

**The dilemma this creates for pool admins:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowed users cannot use the router at all |
| **Allowlist the router** | Every user, including disallowed ones, can bypass the allowlist via the router |

There is no configuration that simultaneously permits router-mediated swaps for allowed users and blocks disallowed users.

**Concrete bypass path:**

1. Pool admin creates a curated pool with `SwapAllowlistExtension` and allowlists `user1`.
2. Pool admin also calls `setAllowedToSwap(pool, router, true)` so that `user1` can use the router.
3. Disallowed `user2` calls `router.exactInputSingle(pool, ...)`.
4. Router calls `pool.swap(recipient, ...)` — `msg.sender` to the pool is `router`.
5. Pool calls `extension.beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. `user2`'s swap executes on the curated pool.

---

### Impact Explanation

The swap allowlist — the primary access-control mechanism for curated pools — is rendered ineffective for any user who routes through the supported periphery. An unprivileged actor bypasses the pool admin's explicit allowlist policy without any special permissions. This is a direct admin-boundary break: the policy the admin configured is silently voided on the router path, which is the standard public entrypoint for swaps.

---

### Likelihood Explanation

Medium-High. Any pool that (a) deploys `SwapAllowlistExtension` to restrict swappers and (b) needs to support router-mediated swaps for those allowed users must allowlist the router. This is the expected operational pattern for curated pools that integrate with the periphery. The bypass requires no privileged access, no special tokens, and no unusual state — only a standard router call.

---

### Recommendation

The extension must check the economically relevant actor, not the immediate pool caller. Two viable approaches:

1. **Router-forwarded identity**: Have the router encode the end-user address into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. The extension must also verify that `msg.sender` (the pool) is a trusted factory-deployed pool so the forwarded identity cannot be spoofed by a direct caller.

2. **Separate `originator` parameter**: Add an `originator` field to the pool's swap interface that the router populates with `msg.sender` before calling the pool, and thread it through `_beforeSwap` alongside `sender`.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists user1 and the router
ext.setAllowedToSwap(pool, user1, true);
ext.setAllowedToSwap(pool, address(router), true); // required for user1 to use router

// Attack: disallowed user2 routes through the router
vm.prank(user2);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        recipient: user2,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Succeeds: extension sees sender=router, allowedSwapper[pool][router]=true
// user2 swaps on the curated pool despite not being allowlisted
``` [4](#0-3) [5](#0-4) [6](#0-5)

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
