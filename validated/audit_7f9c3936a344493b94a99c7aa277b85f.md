### Title
Unchecked `transferFrom` Return Value Enables usdcE Drain Without USDC Payment - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc()` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the boolean return value. Because this function is callable by any unprivileged address on Ink mainnet, an attacker whose `transferFrom` silently returns `false` (rather than reverting) can cause the function to continue execution, draining usdcE from the target DDA and receiving it without having provided any USDC.

---

### Finding Description

`ContractOwner.replaceUsdcEWithUsdc()` is an unrestricted `external` function (no `onlyOwner` modifier) gated only by a chain-ID check (`block.chainid == 57073`). Its purpose is to atomically swap usdcE held in a `DirectDepositV1` (DDA) for native USDC: the caller sends USDC in, the DDA's usdcE is withdrawn to `ContractOwner`, and then forwarded to the caller.

The critical flaw is at line 616:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

`IERC20Base.transferFrom` is declared to return `bool`, but the return value is silently discarded. The protocol uses `ERC20Helper.safeTransferFrom` everywhere else (which checks the return value and reverts on failure), but this specific call uses the raw interface method directly.

If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` on a failed transfer (e.g., insufficient allowance or balance) rather than reverting, execution continues to:

1. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` — transfers the DDA's entire usdcE balance to `ContractOwner` (the DDA's owner).
2. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` — forwards that usdcE to the attacker.

The attacker receives usdcE without having transferred any USDC.

---

### Impact Explanation

**Asset delta:** The entire usdcE balance of any targeted DDA (`directDepositV1Address[subaccount]`) is transferred to the attacker at zero cost. The DDA's usdcE balance is zeroed; the attacker gains it. The subaccount owner loses their pending usdcE deposit that was awaiting credit.

**Corrupted invariant:** The function's atomicity guarantee — "USDC in, usdcE out" — is broken. The usdcE leg executes while the USDC leg silently fails, violating the 1:1 swap assumption the function is designed to enforce.

**Impact: 5/10** — Direct theft of user-deposited usdcE tokens from DDAs, bounded by the usdcE balance of targeted DDAs at the time of the call.

---

### Likelihood Explanation

**Likelihood: 2/10** — The USDC token at the hardcoded address on Ink mainnet is a bridged Circle USDC, which in standard deployments reverts on failure rather than returning `false`. However, the code pattern is unconditionally unsafe: any future token upgrade, redeployment, or token substitution at that address that adopts a non-reverting failure mode would immediately make this exploitable. The entry path requires no privilege and is reachable by any EOA on Ink mainnet.

---

### Recommendation

Replace the raw `transferFrom` call with the protocol's own `safeTransferFrom` wrapper from `ERC20Helper`, consistent with every other token transfer in the codebase:

```solidity
// Before (line 616):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ERC20Helper.safeTransferFrom` checks both the low-level call success and the decoded boolean return value, reverting with `ERR_TRANSFER_FAILED` if either fails.

---

### Proof of Concept

**Root cause location:** [1](#0-0) 

**Unchecked call (line 616):** [2](#0-1) 

**`IERC20Base.transferFrom` returns `bool` (discarded at the call site):** [3](#0-2) 

**Contrast: `ERC20Helper.safeTransferFrom` — the validated wrapper used everywhere else — checks the return value:** [4](#0-3) 

**Attack path:**

1. Identify a DDA (`directDepositV1Address[subaccount]`) with non-zero usdcE balance on Ink mainnet.
2. Call `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance.
3. `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` returns `false`; return value is not checked; execution continues.
4. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` sends usdcE to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` sends usdcE to the attacker.

**Result:** Attacker receives the DDA's full usdcE balance without providing any USDC. The subaccount's pending deposit is permanently lost.

### Citations

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

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
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
