### Title
Unchecked `transferFrom` Return Value Enables Token Drain in `replaceUsdcEWithUsdc` — (`core/contracts/ContractOwner.sol`)

### Summary
`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` without checking the return value. If the transfer silently fails (returns `false`), the function continues to withdraw `usdcE` from the `DirectDepositV1` contract and send it to the caller — effectively draining the DDA's `usdcE` balance without the caller providing any USDC.

### Finding Description
In `replaceUsdcEWithUsdc`, the function is designed as a token-swap helper: the caller provides USDC, the DDA's usdcE is withdrawn and returned to the caller. The three-step sequence is:

1. Pull USDC from `msg.sender` into the DDA — **raw `transferFrom`, return value ignored**
2. Withdraw usdcE from the DDA to `ContractOwner` — uses `DirectDepositV1.withdraw` → `safeTransfer`
3. Push usdcE from `ContractOwner` to `msg.sender` — uses `safeTransfer` [1](#0-0) 

Step 1 uses a bare `.transferFrom()` call with no return-value check: [2](#0-1) 

Steps 2 and 3 use safe wrappers and will succeed regardless of whether step 1 actually moved any tokens. The rest of the codebase consistently uses `ERC20Helper.safeTransferFrom` for all inbound token pulls: [3](#0-2) [4](#0-3) 

### Impact Explanation
If the hardcoded USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` (chain 57073 / Ink) returns `false` on a failed `transferFrom` rather than reverting — which is valid ERC-20 behaviour — any caller can:

- Call `replaceUsdcEWithUsdc(subaccount)` for any DDA that holds a non-zero `usdcE` balance
- Provide zero USDC (step 1 silently fails)
- Receive the full `usdcE` balance of that DDA (steps 2–3 succeed)

The corrupted asset delta is the entire `usdcE` balance of the targeted `DirectDepositV1` contract, transferred to the attacker at zero cost.

### Likelihood Explanation
The function has no access control beyond `block.chainid == 57073`. Any externally-owned account on Ink can call it. The trigger requires only that the USDC token returns `false` on failure rather than reverting. Bridged or wrapped USDC variants on newer chains frequently exhibit this behaviour. The DDA pattern means multiple subaccounts may have funded DDAs, multiplying the attack surface.

### Recommendation
Replace the bare `transferFrom` call with `ERC20Helper.safeTransferFrom`, consistent with every other inbound token transfer in the codebase:

```solidity
// Before (vulnerable)
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe)
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

Also audit the unchecked `approve` calls in `wrapVaultAsset` and `depositInsurance` in the same file for the same class of issue. [5](#0-4) [6](#0-5) 

### Proof of Concept

1. Deploy on chain 57073 (Ink). A DDA for `subaccount` holds 1000 usdcE.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC approval.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, 1000)` returns `false` — no USDC moves.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` succeeds — 1000 usdcE moves to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, 1000)` succeeds — attacker receives 1000 usdcE.
6. Net result: attacker gains 1000 usdcE, DDA is drained, no USDC was provided. [1](#0-0) [7](#0-6)

### Citations

**File:** core/contracts/ContractOwner.sol (L253-254)
```text

        quoteToken.approve(address(endpoint), uint256(amount));
```

**File:** core/contracts/ContractOwner.sol (L529-532)
```text
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
```

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-41)
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
```

**File:** core/contracts/EndpointStorage.sol (L95-101)
```text
    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
