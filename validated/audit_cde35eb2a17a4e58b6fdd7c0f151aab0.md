### Title
Fee-on-Transfer Token Deposit Credits Full `amount` Despite Receiving Less — (`File: core/contracts/EndpointStorage.sol`)

---

### Summary

`handleDepositTransfer` in `EndpointStorage.sol` transfers tokens from the user to the Endpoint and then onward to the Clearinghouse using the same nominal `amount` in both legs, with no balance-delta check. The slow-mode deposit transaction is then enqueued with the original `amount`. When the sequencer later processes it, `Clearinghouse.depositCollateral` credits the user's SpotEngine balance with the full nominal `amount` — even if a fee-on-transfer token silently delivered less. The result is that the protocol's on-chain token holdings are less than the sum of credited balances, creating a solvency shortfall exploitable by any depositor.

---

### Finding Description

`EndpointStorage.handleDepositTransfer` is the single transfer gateway for all collateral deposits:

```solidity
// EndpointStorage.sol lines 111-119
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);          // leg 1: user → Endpoint
    safeTransferTo(token, address(clearinghouse), amount);  // leg 2: Endpoint → Clearinghouse
}
```

`ERC20Helper.safeTransferFrom` only checks the boolean return value of `transferFrom`; it does not measure the actual balance delta:

```solidity
// ERC20Helper.sol lines 38-41
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
```

For a fee-on-transfer token with fee rate `f`:
- Leg 1 delivers `amount × (1 − f)` to the Endpoint; the call still returns `true`.
- Leg 2 attempts to forward the full `amount`. This succeeds only if the Endpoint holds residual balance (e.g., from prior dust or a concurrent deposit). When it does succeed, the Clearinghouse receives `amount × (1 − f)` again.

After both legs, `Endpoint.depositCollateralWithReferral` enqueues a slow-mode transaction carrying the original `amount`:

```solidity
// Endpoint.sol lines 152-164
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
    ...
    tx: abi.encodePacked(
        uint8(TransactionType.DepositCollateral),
        abi.encode(
            DepositCollateral({
                sender: subaccount,
                productId: productId,
                amount: amount          // ← nominal, not actual received
            })
        )
    )
});
```

When the sequencer executes the slow-mode transaction, `Clearinghouse.depositCollateral` credits the full nominal amount:

```solidity
// Clearinghouse.sol lines 205-207
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
```

`txn.amount` is the original `amount`, not what the Clearinghouse actually received. The credited balance exceeds the real token holding by `amount × f` (compounded across both transfer legs).

The same path is reachable through `DirectDepositV1.creditDeposit`, which reads `token.balanceOf(address(this))` and passes it directly as `amount`:

```solidity
// DirectDepositV1.sol lines 90-98
uint256 balance = token.balanceOf(address(this));
if (balance != 0) {
    token.approve(address(endpoint), balance);
    endpoint.depositCollateralWithReferral(
        subaccount, productId, uint128(balance), "-1"
    );
}
```

---

### Impact Explanation

The Clearinghouse's actual token reserve becomes smaller than the aggregate of all credited SpotEngine balances. Users who deposited fee-on-transfer tokens hold inflated collateral balances. When they withdraw, the Clearinghouse transfers the full credited amount out, draining reserves that belong to other depositors. Repeated deposits by multiple users compound the shortfall, eventually making the protocol insolvent for the affected collateral product. The corrupted state variable is `SpotEngine` balance for the affected `productId`, and the Clearinghouse's real ERC-20 holding of that token.

---

### Likelihood Explanation

Likelihood is **medium**. It requires a fee-on-transfer token to be listed as a supported collateral product. The Endpoint's `spotEngine.getToken(productId)` resolves the token address from the SpotEngine config, so any such listing immediately opens the path. The Endpoint has no whitelist guard against fee-on-transfer tokens. The attacker-controlled entry is `depositCollateral` / `depositCollateralWithReferral`, both publicly callable by any address.

---

### Recommendation

Measure the actual balance delta around each transfer leg and use the measured value — not the nominal `amount` — when enqueuing the slow-mode transaction. A minimal fix in `handleDepositTransfer`:

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal returns (uint256 received) {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 before = token.balanceOf(address(clearinghouse));
    safeTransferFrom(token, from, amount);
    safeTransferTo(token, address(clearinghouse), amount);
    received = token.balanceOf(address(clearinghouse)) - before;
    require(received == amount, "fee-on-transfer token not supported");
}
```

Alternatively, document explicitly that fee-on-transfer tokens are unsupported and enforce this at product listing time.

---

### Proof of Concept

1. A fee-on-transfer token `T` (1% fee) is listed as collateral for `productId = P`.
2. The Endpoint holds a small residual balance of `T` (e.g., 1 wei from a prior failed deposit).
3. Attacker calls `Endpoint.depositCollateral("default", P, 1000e18)`.
4. `handleDepositTransfer` is called with `amount = 1000e18`.
   - Leg 1: `safeTransferFrom(T, attacker, Endpoint, 1000e18)` → Endpoint receives `990e18`; call returns `true`.
   - Leg 2: `safeTransferTo(T, Clearinghouse, 1000e18)` → Endpoint has `990e18 + dust ≥ 1000e18`; Clearinghouse receives `990e18`.
5. Slow-mode tx is enqueued with `amount = 1000e18`.
6. Sequencer executes the slow-mode tx; `Clearinghouse.depositCollateral` runs `amountRealized = 1000e18 * multiplier` and calls `spotEngine.updateBalance(P, attacker_subaccount, 1000e18 * multiplier)`.
7. Attacker's SpotEngine balance reflects `1000e18` worth of collateral; Clearinghouse only holds `990e18`. The `10e18` shortfall is a permanent solvency deficit for product `P`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/EndpointStorage.sol (L111-119)
```text
    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/Endpoint.sol (L144-166)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
        slowModeConfig = _slowModeConfig;
```

**File:** core/contracts/Clearinghouse.sol (L193-208)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
```

**File:** core/contracts/DirectDepositV1.sol (L83-100)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
```
