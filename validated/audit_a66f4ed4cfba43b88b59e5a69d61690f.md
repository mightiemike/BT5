### Title
Unattributed Token Accumulation in `DirectDepositV1.creditDeposit()` Permanently Credits Any Sender's Tokens to the DDA Owner's Subaccount — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` is an unrestricted external function that sweeps the entire ERC-20 balance of the contract into a single hardcoded `subaccount`. Because the contract holds no per-sender accounting and provides no refund path for non-owner depositors, any tokens sent to the DDA by a party other than the intended owner are permanently credited to the DDA owner's on-chain subaccount with no recovery mechanism.

---

### Finding Description

`DirectDepositV1` is deployed as a personal deposit address for a specific `subaccount` fixed at construction time. The `creditDeposit()` function is `external` with no access modifier: [1](#0-0) 

It iterates over every product ID registered in the spot engine, reads the contract's full ERC-20 balance for each token, approves the endpoint, and calls `depositCollateralWithReferral` with the hardcoded `subaccount`: [2](#0-1) 

There is no mapping, event, or state variable that records which external address sent which tokens. The contract simply holds whatever ERC-20 balance it has accumulated and, when `creditDeposit()` is called, attributes the entire balance to the single owner subaccount.

The only withdrawal path for tokens sitting in the contract is `withdraw()`, which is `onlyOwner`: [3](#0-2) 

This mirrors the `BaseAsyncSwap` pattern exactly: tokens accumulate in the contract from any source, there is no per-user attribution, and the only party who can act on those tokens is the contract owner.

---

### Impact Explanation

If a user (User B) sends ERC-20 tokens to a DDA that belongs to a different subaccount (User A), the following occurs:

1. User B's tokens sit in User A's `DirectDepositV1` contract.
2. Anyone — including User A — calls `creditDeposit()`.
3. `endpoint.depositCollateralWithReferral(subaccount_A, ...)` is called; the endpoint pulls the full balance from the DDA and credits it to `subaccount_A`.
4. User B's tokens are now inside User A's on-chain subaccount balance in `SpotEngine`.
5. User B has no on-chain mechanism to reclaim them. The `withdraw()` function is `onlyOwner` (User A), and `creditDeposit()` has already moved the tokens into the exchange's custody under User A's name.

The corrupted state delta is: `SpotEngine.balances[subaccount_A][productId]` is inflated by User B's deposit amount, while User B receives nothing. [4](#0-3) 

---

### Likelihood Explanation

Each user is assigned a unique DDA address. Realistic triggers include:

- A UI bug or misconfiguration that displays the wrong DDA address to a user.
- A user copying a DDA address from a shared context (e.g., a block explorer search result for a similarly named subaccount).
- A phishing page that substitutes the attacker's DDA address for the victim's.

Once tokens land in the wrong DDA, the DDA owner has a direct, permissionless path (`creditDeposit()`) to absorb them into their subaccount. No privileged role is required. The attacker's only action is calling a public function.

---

### Recommendation

1. **Add per-sender accounting**: Record `(sender → productId → amount)` when tokens are received (e.g., via a `deposit(uint32 productId, uint128 amount)` entry point that pulls tokens from `msg.sender` and records the attribution).
2. **Restrict `creditDeposit()` to `onlyOwner`**: Since the function's only legitimate caller is the DDA owner, adding `onlyOwner` eliminates the permissionless sweep.
3. **Emit events on receipt**: At minimum, emit an event when `creditDeposit()` is called so off-chain tooling can detect and flag unexpected token inflows.

---

### Proof of Concept

```
1. Deploy DirectDepositV1 for subaccount_A (attacker-controlled).
2. Victim (User B) sends 1000 USDC to the DirectDepositV1 address
   (e.g., due to a UI bug showing the wrong DDA address).
3. Attacker calls DirectDepositV1.creditDeposit().
   - spotEngine.getProductIds() returns [0] (USDC product).
   - token.balanceOf(address(this)) == 1000e6.
   - token.approve(endpoint, 1000e6).
   - endpoint.depositCollateralWithReferral(subaccount_A, 0, 1000e6, "-1").
   - Endpoint pulls 1000 USDC from DDA and credits subaccount_A.
4. SpotEngine balance for subaccount_A increases by 1000 USDC.
5. User B's 1000 USDC is permanently lost; no on-chain recovery path exists.
``` [1](#0-0) [5](#0-4)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L83-101)
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
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
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

**File:** core/contracts/Endpoint.sol (L123-167)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

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
    }
```
