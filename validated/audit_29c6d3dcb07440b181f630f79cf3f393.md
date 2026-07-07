### Title
Unrestricted `creditDeposit()` Allows Any Caller to Force-Deposit DDA Tokens Into the Protocol, Bypassing Owner Recovery Path - (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary
`DirectDepositV1.creditDeposit()` is an `external` function with no access control. Any unprivileged caller can invoke it to sweep all token balances held by a Direct Deposit Address (DDA) contract into the protocol on behalf of the associated subaccount. This permanently removes the owner's ability to recover those tokens via the `onlyOwner`-gated `withdraw()` / `withdrawNative()` escape hatches.

---

### Finding Description
`DirectDepositV1` is a per-subaccount deposit proxy contract. It holds tokens sent by users and exposes:

- `creditDeposit()` — `external`, **no modifier** — iterates all spot product IDs, approves the endpoint for the full balance of each token, and calls `depositCollateralWithReferral()` for the hardcoded `subaccount`.
- `withdraw(IIERC20Base token)` — `external onlyOwner` — recovers a specific token to the owner.
- `withdrawNative()` — `external onlyOwner` — recovers native balance to the owner. [1](#0-0) 

Because `creditDeposit()` carries no `onlyOwner` or equivalent guard, any EOA or contract can call it at any time. Once called, every token balance in the DDA is approved and deposited into the protocol. The owner's `withdraw()` path is then useless — the balance is zero. [2](#0-1) 

The parallel to the 4pool `skim()` bug is exact in structure: one privileged path (`withdraw`) and one unguarded path (`creditDeposit`) operate on the same pool of tokens, and the unguarded path can be triggered by anyone to pre-empt the privileged one.

---

### Impact Explanation
The owner of a `DirectDepositV1` contract is the deploying `ContractOwner` contract (the protocol team). [3](#0-2) 

The `withdraw()` escape hatch exists precisely so the protocol team can recover tokens that were sent to a DDA but should not be deposited — for example: tokens sent to the wrong DDA, unsupported token types that happen to match a product ID, or tokens the team needs to redirect before a deposit is processed. An attacker who calls `creditDeposit()` first forces all such tokens into the protocol under the subaccount, making them unrecoverable by the owner without going through the full protocol withdrawal flow (which requires the subaccount owner's cooperation and signature). The protocol team's unilateral recovery capability is permanently destroyed for that DDA instance.

Concrete corrupted state: `token.balanceOf(dda)` transitions from a non-zero recoverable amount to zero; `fees[productId]` and subaccount balances in `SpotEngine` are updated without the owner's consent.

---

### Likelihood Explanation
- Every deployed DDA is a publicly visible contract on-chain; its address and token balances are observable.
- The call requires no special permissions, no signatures, and no capital.
- A griefing attacker or a subaccount owner who wants to prevent the protocol team from clawing back tokens can call `creditDeposit()` at any time, including as a front-run against a pending `withdraw()` transaction.
- Likelihood: **Medium** — requires monitoring DDA contracts and a motivation to act, but the execution cost is a single zero-argument external call.

---

### Recommendation
Apply an `onlyOwner` modifier to `creditDeposit()`, consistent with the access control already applied to `withdraw()` and `withdrawNative()`:

```solidity
// Before
function creditDeposit() external {

// After
function creditDeposit() external onlyOwner {
```

Alternatively, introduce a separate trusted-caller role (analogous to the `onlyOwnerOrRebaseHandler` fix in the 4pool report) if automated keepers need to call `creditDeposit()` without full owner privileges. [1](#0-0) 

---

### Proof of Concept
1. Protocol team deploys a `DirectDepositV1` for `subaccount = alice.eth`.
2. A user accidentally sends 10,000 USDC to the DDA address.
3. Protocol team prepares a `withdraw(USDC)` transaction to recover the funds.
4. Attacker observes the pending transaction in the mempool and front-runs it by calling `dda.creditDeposit()`.
5. `creditDeposit()` approves the endpoint for 10,000 USDC and calls `depositCollateralWithReferral(alice_subaccount, productId, 10000e6, "-1")`.
6. The 10,000 USDC is now credited to Alice's subaccount in `SpotEngine`.
7. The protocol team's `withdraw()` call executes but transfers 0 tokens (balance is now zero).
8. The protocol team has permanently lost unilateral recovery of those funds; retrieval now requires Alice's signed cooperation through the normal withdrawal flow. [1](#0-0) [2](#0-1)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L29-51)
```text
contract DirectDepositV1 is Ownable {
    IIEndpoint internal endpoint;
    IISpotEngine internal spotEngine;
    bytes32 internal subaccount;
    address payable internal wrappedNative;

    event NativeTokenTransferFailed(uint256 amount);
    event DirectDepositV1Created(
        uint8 indexed version,
        bytes32 indexed subaccount,
        address dda
    );

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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
