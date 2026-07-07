### Title
`DirectDepositV1` Has No Mechanism to Withdraw Deposited Funds From the Protocol, Permanently Locking Assets When the Subaccount Is the Contract's Own Address — (`core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1` is a contract that acts as a deposit relay into the Nado protocol. It holds a fixed `subaccount` (set at construction) and exposes `creditDeposit()` to push all held token balances into the protocol under that subaccount. However, the contract has **no function to withdraw those funds back out of the protocol**. The only withdrawal functions (`withdraw()` and `withdrawNative()`) operate on tokens held by the DDA contract itself — not on balances already deposited into the protocol. When the `subaccount` is set to the DDA contract's own address-based subaccount (a valid use case for protocol-owned accounts or smart-contract wallets), the deposited funds are permanently locked because the DDA contract cannot sign EIP-712 withdrawal transactions and has no built-in path to initiate one.

---

### Finding Description

`DirectDepositV1` is deployed with a fixed `subaccount` (a `bytes32`) and an `owner`. Its `creditDeposit()` function iterates over all spot product tokens, approves the `Endpoint`, and calls `depositCollateralWithReferral` to credit the fixed `subaccount`: [1](#0-0) 

Once `creditDeposit()` is called, the tokens leave the DDA contract and are credited to `subaccount` inside the protocol's `SpotEngine`. The only way to retrieve them is to submit a signed `WithdrawCollateral` transaction through the `Endpoint` (either via the sequencer fast path or the slow-mode queue). Both paths require a valid EIP-712 signature from the subaccount owner or a linked signer.

The DDA contract's two withdrawal functions only drain the DDA contract's own ERC-20 or native balance — they do not interact with the protocol at all: [2](#0-1) 

There is no `withdrawFromProtocol()`, no `submitSlowModeTransaction()` wrapper, and no `linkSigner()` helper. The DDA contract is entirely incapable of signing or submitting a withdrawal transaction on its own behalf.

When the `subaccount` constructor argument is set to the DDA contract's own address-based subaccount — a realistic scenario for protocol-owned reserve accounts, smart-contract wallets, or automated market-making bots — the DDA contract is the on-chain owner of that subaccount. Because it cannot sign, it cannot withdraw, and the deposited collateral is permanently stuck inside the protocol. [3](#0-2) 

---

### Impact Explanation

Any ERC-20 collateral deposited via `creditDeposit()` into a subaccount whose address matches the DDA contract itself is irrecoverable. The `Clearinghouse.withdrawCollateral` path requires the `Endpoint` to process a signed transaction; the DDA contract has no mechanism to produce or submit one. The `WithdrawPool.removeLiquidity` path is `onlyOwner` of the pool, not of the DDA. There is no escape hatch. [4](#0-3) 

---

### Likelihood Explanation

The `subaccount` parameter is a raw `bytes32` passed at construction with no validation. Protocol-owned reserve accounts, automated bots, and smart-contract wallet integrations routinely set the subaccount to the deploying contract's own address. Any such deployment that subsequently calls `creditDeposit()` triggers the permanent lock. The trigger requires no privileged access and no external attacker — it is a consequence of normal, intended usage of the contract. [5](#0-4) 

---

### Recommendation

Add a `withdrawFromProtocol` function (or equivalent slow-mode submission helper) to `DirectDepositV1` that constructs and submits a `WithdrawCollateral` slow-mode transaction on behalf of the DDA's subaccount. Alternatively, add a `linkSigner` helper that submits a `LinkSigner` transaction so that an authorized EOA can sign withdrawals on behalf of the DDA's subaccount. At minimum, document clearly that the `subaccount` must never be set to the DDA contract's own address, and add a constructor-time check enforcing this.

---

### Proof of Concept

1. Deploy `DirectDepositV1` with `_subaccount = bytes32(uint256(uint160(address(dda))))` (DDA contract's own address-based subaccount).
2. Transfer 1000 USDC to the DDA contract.
3. Call `dda.creditDeposit()`. The 1000 USDC is deposited into the protocol under the DDA's own subaccount.
4. Attempt to recover funds: `dda.withdraw(usdc)` returns 0 (no balance left in DDA). There is no `withdrawFromProtocol()` function.
5. Attempt slow-mode withdrawal: requires a signed `WithdrawCollateral` transaction from the DDA contract — impossible, as the DDA has no signing capability.
6. Funds are permanently locked in the protocol. [1](#0-0) [6](#0-5)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L42-61)
```text
    constructor(
        address _endpoint,
        address _spotEngine,
        bytes32 _subaccount,
        address payable _wrappedNative
    ) {
        endpoint = IIEndpoint(_endpoint);
        spotEngine = IISpotEngine(_spotEngine);
        subaccount = _subaccount;
        wrappedNative = _wrappedNative;
        uint256 balance = address(this).balance;
        if (balance != 0) {
            // shouldn't revert even if the transfer fails, otherwise the funds
            // will be stuck in the DDA forever.
            (bool success, ) = wrappedNative.call{value: balance}("");
            if (!success) {
                emit NativeTokenTransferFailed(balance);
            }
        }
        emit DirectDepositV1Created(version(), subaccount, address(this));
```

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

**File:** core/contracts/DirectDepositV1.sol (L103-112)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }

    function withdrawNative() external onlyOwner {
        uint256 balance = address(this).balance;
        (bool success, ) = msg.sender.call{value: balance}("");
        require(success, "Failed to transfer native token to owner");
    }
```

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```
