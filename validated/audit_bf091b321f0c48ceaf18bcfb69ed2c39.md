### Title
SwapAllowlistExtension gates on the router's address (`sender`) rather than the end user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension` is documented as gating `swap` **by swapper address**. Its `beforeSwap` hook checks `sender`, which is the `msg.sender` of the `pool.swap()` call — i.e., the router contract — not the ultimate end user. When the pool admin allowlists the router (required for any router-based swap to succeed), every user on the network can bypass the allowlist by routing through `MetricOmmSimpleRouter`, regardless of whether their own address is permitted.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`sender` is populated by the pool from its own `msg.sender` — the direct caller of `pool.swap()`. When a user goes through `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller:

```solidity
// MetricOmmSimpleRouter.sol L71-80
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

The pool therefore calls `_beforeSwap(router, recipient, ...)`, and the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

The same pattern applies to multi-hop `exactInput` and `exactOutput` paths.

**The phantom-levels analog:** In Hats Protocol, a non-existent intermediate hatId (phantom level) was used to bypass the 65,536-children-per-level constraint because the validation did not verify that every node in the path was real. Here, the router is the "phantom intermediate node": it is a real contract, but it is not the entity the allowlist is meant to gate. The allowlist validation does not traverse back to the actual initiating user, so the constraint is bypassed whenever the router sits between the user and the pool.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is typically a restricted pool — e.g., KYC-gated, institutional-only, or limited to specific counterparties to prevent toxic flow and LP value leakage. Once the pool admin allowlists the router (which they must do to allow any router-based swap), the allowlist provides zero protection: every address on the network can call `router.exactInputSingle()` and swap freely. Unauthorized counterparties can extract value from LPs through adverse selection, directly causing LP principal loss. This satisfies the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "direct loss of user principal" impact criteria.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the canonical user-facing entry point for swaps.
- Any pool that uses `SwapAllowlistExtension` and also wants to support router-based swaps **must** allowlist the router — there is no other path.
- Once the router is allowlisted, the bypass is trivially reachable by any EOA or contract with no special privileges.
- The admin has no mechanism to simultaneously allow router-based swaps for approved users and block router-based swaps for unapproved users, because the extension does not expose the originating user's address.

---

### Recommendation

The extension must check the **originating user**, not the immediate caller. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.

2. **Check `recipient` instead of (or in addition to) `sender`**: For single-hop swaps, `recipient` is the address that receives output tokens and is a better proxy for the actual beneficiary. However, for multi-hop swaps the intermediate recipient is the router itself, so this is also imperfect.

3. **Preferred — add a `swapper` field to the pool's swap interface**: The pool should accept an explicit `swapper` address (set by the router to `msg.sender` before calling the pool) and pass it to extensions as a distinct parameter, separate from `sender` (the contract calling `swap`) and `recipient` (the output destination).

---

### Proof of Concept

1. Pool `P` is deployed with `SwapAllowlistExtension` as `EXTENSION_1` on `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(P, router, true)` to enable router-based swaps for approved users.
3. Admin does **not** call `setAllowedToSwap(P, attacker, true)`.
4. Attacker (not allowlisted) calls:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
       pool: P,
       recipient: attacker,
       zeroForOne: true,
       amountIn: X,
       ...
   }));
   ```
5. Router calls `P.swap(attacker, true, X, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, attacker, ...)`.
7. Extension evaluates `allowedSwapper[P][router] == true` → passes.
8. Attacker's swap executes successfully despite not being on the allowlist. [1](#0-0) [2](#0-1) [3](#0-2)

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
