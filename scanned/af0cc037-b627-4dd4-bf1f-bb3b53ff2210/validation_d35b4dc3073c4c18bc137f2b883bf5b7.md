### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any unprivileged user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router (a necessary step to enable any router-mediated swap on a curated pool), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point), the router calls `pool.swap()` with itself as `msg.sender`: [4](#0-3) 

The extension therefore checks whether the **router** is allowlisted, not whether the **end user** is allowlisted. The pool admin who wants allowlisted users to be able to use the router must add the router to `allowedSwapper[pool]`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every call that arrives through the router, regardless of who the actual end user is. The end user's identity is never examined.

The allowlist storage is keyed `allowedSwapper[pool][swapper]` and the admin setter operates on individual addresses: [5](#0-4) 

There is no mechanism in the extension to recover the original caller from `extensionData` or any other channel, so the identity mismatch is structural.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swapping to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or regulatory-compliant participants) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker receives the full output token amount from the pool without being on the allowlist. This is a direct bypass of the pool's access-control invariant with fund-impacting consequences: the pool transacts with counterparties the admin explicitly excluded, and LP value is exposed to unintended swap flow.

---

### Likelihood Explanation

The scenario requires the pool admin to have added the router to `allowedSwapper[pool]`. This is a natural and expected administrative action: without it, no allowlisted user can use the router either, making the router useless for that pool. Any pool that intends to support router-mediated swaps while also enforcing a per-user allowlist will reach this configuration. The attacker needs only to call the public router with a valid swap; no special privileges, flash loans, or oracle manipulation are required.

---

### Recommendation

The extension must verify the ultimate end user, not the intermediary. Two approaches:

1. **Pass the real user through `extensionData`**: Require the router to encode the actual `msg.sender` (the end user) into `extensionData` before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of (or in addition to) `sender`.

2. **Check `sender` AND require `sender == tx.origin` for direct calls**: This is fragile and generally discouraged, but illustrates the identity problem.

The cleanest fix is approach 1: the router encodes the payer/user address into `extensionData`, and the extension reads it. The pool already forwards `extensionData` unmodified to every extension hook, so no core changes are needed.

---

### Proof of Concept

```
Setup:
  pool P has SwapAllowlistExtension E configured
  admin allowlists router R: allowedSwapper[P][R] = true
  user Alice (0xAlice) is NOT in allowedSwapper[P]

Attack:
  Alice calls MetricOmmSimpleRouter.exactInputSingle({
    pool: P,
    tokenIn: token0,
    ...
  })

  Router calls P.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    → msg.sender = Router (R)

  Pool calls E.beforeSwap(sender=R, ...)
    → allowedSwapper[P][R] == true  ✓  (check passes)

  Swap executes; Alice receives output tokens.

Result:
  Alice, who is not on the allowlist, successfully swaps on a curated pool.
  The allowlist guard is fully bypassed.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-29)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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
