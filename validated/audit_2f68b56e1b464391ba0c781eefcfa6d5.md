### Title
Missing Before-After Balance Check in `depositCollateralWithReferral` Allows Subaccount Over-Crediting for Fee-on-Transfer Tokens — (File: `core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint.depositCollateralWithReferral` pulls tokens from the caller via `handleDepositTransfer` and then enqueues a slow-mode transaction recording the caller-supplied `amount` parameter verbatim. When the sequencer later executes that slow-mode transaction, `Clearinghouse.depositCollateral` credits `amountRealized` derived from `txn.amount` — the original parameter — without any before-after balance check to confirm how many tokens were actually received. For fee-on-transfer tokens, the actual received amount is less than `amount`, yet the subaccount is credited the full `amount`, inflating its balance and draining the protocol's real token reserves.

---

### Finding Description

In `Endpoint.depositCollateralWithReferral`, the flow is:

1. `handleDepositTransfer(token, msg.sender, uint256(amount))` — pulls tokens from the caller.
2. A `SlowModeTx` is enqueued with `DepositCollateral{ sender: subaccount, productId: productId, amount: amount }` — the original caller-supplied value. [1](#0-0) 

When the sequencer processes this slow-mode transaction, `Clearinghouse.depositCollateral` executes:

```solidity
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
``` [2](#0-1) 

There is no snapshot of the contract's token balance before `handleDepositTransfer` and no comparison after it. The credited `amountRealized` is derived entirely from the parameter `txn.amount`, not from the actual delta in the contract's token holdings.

For a fee-on-transfer ERC20 token listed as a supported product, `handleDepositTransfer` receives `amount - fee_taken_by_token`, but the slow-mode record carries the full `amount`. The subaccount is therefore credited `amount` worth of internal balance while the contract only holds `amount - fee`.

A secondary instance of the same pattern exists in `BaseWithdrawPool.submitFastWithdrawal`. When `sendTo != msg.sender`, the fast-withdrawal fee is collected via:

```solidity
safeTransferFrom(token, msg.sender, uint128(fee));
fees[productId] += fee;
``` [3](#0-2) 

No before-after balance check guards the `safeTransferFrom` call. For a fee-on-transfer token, `fees[productId]` is incremented by the full `fee` even though only `fee - token_fee` was actually received, inflating the tracked fee balance.

---

### Impact Explanation

Every deposit of a fee-on-transfer token creates a gap between the protocol's real token holdings and the sum of all subaccount credits. Over repeated deposits the gap compounds: the protocol owes more tokens than it holds. When users withdraw, later withdrawers cannot be paid in full, resulting in direct loss of funds for those users and insolvency of the affected product's collateral pool.

The `fees[productId]` inflation in `submitFastWithdrawal` similarly causes the protocol to believe it holds more fee revenue than it does, leading to failed or under-funded fee claims.

---

### Likelihood Explanation

`depositCollateralWithReferral` is `public` and callable by any unprivileged address, including via `DirectDepositV1.creditDeposit()`. [4](#0-3) 

The trigger requires a fee-on-transfer token to be registered as a supported product. Fee-on-transfer tokens are a well-known ERC20 variant (e.g., USDT on some chains, STA, PAXG). If any such token is ever listed — intentionally or by oversight — the vulnerability is immediately exploitable by any depositor. Likelihood is **Medium**: the code path is always open; exploitation depends on token listing choices.

---

### Recommendation

Capture the contract's token balance before and after `handleDepositTransfer` and use the actual delta as the credited amount, mirroring the pattern already used elsewhere in the protocol for `_assetRecipient` checks:

```solidity
uint256 balanceBefore = token.balanceOf(address(this));
handleDepositTransfer(token, msg.sender, uint256(amount));
uint256 actualReceived = token.balanceOf(address(this)) - balanceBefore;
// use actualReceived (cast to uint128) in the SlowModeTx instead of amount
```

Apply the same before-after check around `safeTransferFrom` in `BaseWithdrawPool.submitFastWithdrawal` before incrementing `fees[productId]`.

---

### Proof of Concept

1. A fee-on-transfer token `FoT` (2% transfer fee) is listed as a supported product with `productId = X`.
2. Attacker calls `Endpoint.depositCollateral("default", X, 1000e18)`.
3. `handleDepositTransfer` pulls `1000e18` from the attacker; the contract receives `980e18` (2% fee taken by the token).
4. A `SlowModeTx` is enqueued recording `amount = 1000e18`.
5. The sequencer executes the slow-mode transaction; `Clearinghouse.depositCollateral` credits `1000e18 * multiplier` to the attacker's subaccount.
6. The attacker's subaccount now shows `1000e18` of internal balance backed by only `980e18` of real tokens.
7. Repeated across many depositors, the protocol's real holdings fall short of total subaccount credits; the last withdrawers cannot be made whole. [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/Endpoint.sol (L123-128)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
```

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

**File:** core/contracts/Clearinghouse.sol (L193-209)
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
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L104-113)
```text
        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
```
