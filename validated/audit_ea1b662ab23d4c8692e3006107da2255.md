### Title
Deflationary Token Deposit Inflates Subaccount Credit Beyond Actual Collateral Received — (`core/contracts/EndpointStorage.sol`)

---

### Summary

`handleDepositTransfer` in `EndpointStorage.sol` performs two sequential token transfers using the caller-supplied `amount` without checking the actual balance received at each hop. When a deflationary (fee-on-transfer) token is used as a collateral product, the clearinghouse receives fewer tokens than `amount`, but the slow-mode deposit transaction queued in `Endpoint.depositCollateralWithReferral` records the original `amount`. When the sequencer later processes that slow-mode transaction, `Clearinghouse.depositCollateral` credits the subaccount with the full `amount`, creating an unbacked balance.

---

### Finding Description

The deposit flow is:

**Step 1 — `Endpoint.depositCollateralWithReferral`** calls `handleDepositTransfer` with the user-supplied `amount`, then immediately queues a `SlowModeTx` encoding `DepositCollateral{amount: amount}`: [1](#0-0) 

**Step 2 — `EndpointStorage.handleDepositTransfer`** pulls `amount` from the user to the Endpoint, then pushes `amount` from the Endpoint to the clearinghouse — both using the same nominal value, with no before/after balance check: [2](#0-1) 

**Step 3 — `ERC20Helper.safeTransferFrom`** only checks the boolean return value; it does not measure how many tokens were actually received: [3](#0-2) 

**Step 4 — `Clearinghouse.depositCollateral`** (called when the sequencer processes the slow-mode tx) credits `txn.amount` — the original user-specified value — to the subaccount via `spotEngine.updateBalance`: [4](#0-3) 

For a token that charges a transfer fee on `transfer` (but not on `transferFrom`):
- `safeTransferFrom(user → endpoint, amount)` succeeds; endpoint holds `amount`.
- `safeTransferTo(endpoint → clearinghouse, amount)` succeeds; clearinghouse receives `amount − fee`.
- The slow-mode tx records `amount`.
- `depositCollateral` credits the subaccount with `amount`.

The clearinghouse's actual token reserve is `amount − fee`, but the subaccount's credited balance is `amount`. The gap is never reconciled.

The same path exists for `DepositInsurance` in `EndpointTx.submitSlowModeTransactionImpl`: [5](#0-4) 

---

### Impact Explanation

The subaccount is credited with more collateral than the clearinghouse actually holds. The user can immediately use the inflated balance to open leveraged positions or withdraw collateral. Across many deposits, the cumulative shortfall grows, making the protocol insolvent: the sum of all credited spot balances exceeds the actual token reserves held by the clearinghouse. Other users' withdrawals will eventually fail.

---

### Likelihood Explanation

Likelihood is conditional on a fee-on-transfer token being listed as a collateral product by the owner. Once listed, any unprivileged depositor can trigger the discrepancy on every deposit call to `depositCollateral` / `depositCollateralWithReferral`. No special permissions or front-running are required beyond the token being listed.

---

### Recommendation

Replace the fixed-`amount` two-hop transfer in `handleDepositTransfer` with a balance-delta check. Measure the clearinghouse's token balance before and after the transfer sequence, and use the delta as the amount passed to the slow-mode transaction:

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 before = token.balanceOf(address(clearinghouse));
    safeTransferFrom(token, from, amount);
    safeTransferTo(token, address(clearinghouse), amount);
    uint256 actualReceived = token.balanceOf(address(clearinghouse)) - before;
    // use actualReceived (not amount) when encoding the SlowModeTx
}
```

Alternatively, document explicitly that fee-on-transfer and rebasing tokens are not supported collateral assets, and add an on-chain guard (e.g., a whitelist check or a `require(actualReceived == amount)`) to enforce this invariant at deposit time.

---

### Proof of Concept

1. Owner lists a fee-on-transfer ERC20 (1% fee on `transfer`) as `productId = 5`.
2. Attacker calls `Endpoint.depositCollateralWithReferral(subaccount, 5, 1000e6, "")`.
3. `handleDepositTransfer` pulls 1000e6 from attacker → Endpoint (no fee on `transferFrom`), then pushes 1000e6 from Endpoint → clearinghouse (1% fee deducted): clearinghouse receives 990e6.
4. A `SlowModeTx` encoding `DepositCollateral{amount: 1000e6}` is queued.
5. Sequencer processes the slow-mode tx; `Clearinghouse.depositCollateral` calls `spotEngine.updateBalance(5, subaccount, 1000e6_normalized)`.
6. Attacker's subaccount shows 1000e6 of collateral; clearinghouse holds only 990e6.
7. Attacker withdraws 1000e6; clearinghouse is short 10e6, draining reserves belonging to other users.

### Citations

**File:** core/contracts/Endpoint.sol (L144-165)
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
```

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

**File:** core/contracts/EndpointTx.sol (L345-354)
```text
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
            IEndpoint.DepositInsurance memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositInsurance)
            );
            require(
                txn.amount >= uint128(SLOW_MODE_FEE),
                ERR_DEPOSIT_TOO_SMALL
            );
            handleDepositTransfer(_getQuote(), sender, uint256(txn.amount));
```
